"""Exercise the process entry point against the current web factory signature."""
from types import SimpleNamespace
from unittest.mock import patch
from scripts.start_server import run_web_mode


def test_web_process_starts_with_current_factory_contract(tmp_path):
    args = SimpleNamespace(control_file=str(tmp_path/'control.json'),status_file=str(tmp_path/'status.json'),
                           host='127.0.0.1',port=5000,strips=33,leds_per_strip=138,
                           animations_dir='animation/plugins',animation_speed_scale=.3,debug=False)
    with patch('web.app.create_app', autospec=True) as factory:
        run_web_mode(args)
        factory.return_value.run.assert_called_once_with(debug=False)


def test_controller_process_publishes_status_and_polls_real_queues(tmp_path, monkeypatch):
    import json
    import pytest
    import sys
    if "spidev" not in sys.modules:
        monkeypatch.setitem(sys.modules,"spidev",SimpleNamespace(SpiDev=object))
    import drivers.multi_device
    import scripts.start_server as server
    from animation.core.manager import PreviewLEDController
    controller=PreviewLEDController(33,138)
    controller.close=lambda:None
    probes=[]
    controller.refresh_receiver_status=lambda:probes.append(True)
    monkeypatch.setattr(drivers.multi_device,'MultiDeviceLEDController',lambda **kwargs:controller)
    def stop_after_first_iteration(_seconds):raise KeyboardInterrupt
    monkeypatch.setattr(server.time,'sleep',stop_after_first_iteration)
    args=SimpleNamespace(strips=33,leds_per_strip=138,saved_state_file=str(tmp_path/'settings.json'),
                         presets_dir=str(tmp_path/'presets'),controller_debug=False,spi_speed=20000000,
                         brightness=0,animation_speed_scale=.3,animations_dir='animation/plugins',
                         target_fps=150,control_file=str(tmp_path/'control.json'),status_file=str(tmp_path/'status.json'),
                         status_interval=.5,poll_interval=.05)
    with pytest.raises(KeyboardInterrupt):server.run_controller_mode(args)
    status=json.loads((tmp_path/'status.json').read_text())
    assert probes==[True]
    assert status['brightness']==0
    assert status['is_running'] is False
    assert status['written_at']>0
    assert (tmp_path/'playlists/current.json').exists()


def test_file_controller_reports_successful_setting_id_for_home_assistant(tmp_path, monkeypatch):
    import json, sys
    import pytest
    if "spidev" not in sys.modules:
        monkeypatch.setitem(sys.modules,"spidev",SimpleNamespace(SpiDev=object))
    import drivers.multi_device
    import scripts.start_server as server
    from animation.core.manager import PreviewLEDController
    from ipc.control_channel import FileControlChannel
    controller=PreviewLEDController(33,138);controller.close=lambda:None
    monkeypatch.setattr(drivers.multi_device,'MultiDeviceLEDController',lambda **kwargs:controller)
    channel=FileControlChannel(tmp_path/'control.json',tmp_path/'status.json')
    sent=[]
    def next_iteration(_seconds):
        if len(sent)==0:sent.append(channel.send_command('set_output_brightness',brightness=42))
        elif len(sent)==1:sent.append(channel.send_command('set_output_brightness',brightness=999))
        else:raise KeyboardInterrupt
    monkeypatch.setattr(server.time,'sleep',next_iteration)
    args=SimpleNamespace(strips=33,leds_per_strip=138,saved_state_file=str(tmp_path/'settings.json'),
                         presets_dir=str(tmp_path/'presets'),controller_debug=False,spi_speed=20000000,
                         brightness=0,animation_speed_scale=.3,animations_dir='animation/plugins',
                         target_fps=150,control_file=str(tmp_path/'control.json'),status_file=str(tmp_path/'status.json'),
                         status_interval=0,poll_interval=.05)
    with pytest.raises(KeyboardInterrupt):server.run_controller_mode(args)
    status=json.loads((tmp_path/'status.json').read_text())
    assert status['brightness']==42
    assert status['last_applied_command_id']==sent[0]['command_id']
    assert status['last_command_id']==sent[1]['command_id']
    assert status['command_result']['state']=='failed'


def test_receiver_refresh_reports_command_success_without_display_guarantee():
    from scripts.start_server import _dispatch_legacy_command
    from unittest.mock import Mock
    controller=SimpleNamespace(refresh_receiver_status=Mock(return_value={'devices':[]}))
    assert _dispatch_legacy_command(SimpleNamespace(controller=controller),'refresh_receiver_status',{}) is True
    controller.refresh_receiver_status.assert_called_once_with(None)

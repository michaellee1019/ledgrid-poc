"""Queued writes retain ordering, correlation, and manual playlist takeover."""
from copy import deepcopy
from pathlib import Path
import uuid
import pytest
from ipc.control_channel import FileControlChannel
from ipc.runtime_control import ControllerCommandCoordinator, ControllerCommandConflictError
from ipc.playlist_runtime import PlaylistRunner

class Manager:
    def __init__(self):
        self.scene=None
        self.is_running=False
        self.started=[]
    def get_scene_state(self):return deepcopy(self.scene)
    def stop_animation(self):self.is_running=False;return True


def command(owner,scene):
    return {'schema':'ledgrid.playlist-command','schema_version':1,'request_id':str(uuid.uuid4()),
        'run_id':str(uuid.uuid4()),'action':'start','requested_at':0,'playlist_name':'Two Scenes',
        'expected_controller_session_id':owner.session_id,'expected_controller_state_revision':owner.state_revision,
        'entries':[{'entry_id':'one','label':'First','duration_seconds':1,'scene':scene},
                   {'entry_id':'two','label':'Second','duration_seconds':1,'scene':{'name':'second'}}]}


def test_commands_are_queued_in_order_and_results_are_request_correlated(tmp_path):
    channel=FileControlChannel(str(tmp_path/'control.json'),str(tmp_path/'status.json'))
    first=channel.send_command('stop');second=channel.send_command('set_output_brightness',brightness=0)
    assert [x['request_id'] for x in channel.poll_commands()]==[first['request_id'],second['request_id']]
    result={'request_id':first['request_id'],'command_id':first['command_id'],'state':'completed'}
    channel.acknowledge_command(first,result)
    assert channel.read_command_result(first['request_id'])==result
    assert channel.poll_commands()==[second]


def test_failed_command_advances_revision_and_preserves_partial_state():
    manager=Manager();owner=ControllerCommandCoordinator(manager)
    with pytest.raises(RuntimeError):
        with owner.legacy_mutation_guard():
            manager.scene={'partial':True}
            raise RuntimeError('send failed')
    assert owner.state_revision==1
    assert owner.selected_scene=={'partial':True}
    with pytest.raises(ControllerCommandConflictError):
        with owner.legacy_mutation_guard({'expected_controller_session_id':'old','expected_controller_state_revision':0,'expires_at':9999999999}):pass


def test_playlist_stops_on_failure_without_retry_or_restore(monkeypatch):
    import ipc.playlist_runtime as runtime
    manager=Manager();owner=ControllerCommandCoordinator(manager);clock=[0.0]
    monkeypatch.setattr(runtime,'normalize_managed_scene',lambda manager,scene:deepcopy(scene))
    def start(manager,scene):
        manager.scene=deepcopy(scene);manager.started.append(deepcopy(scene));manager.is_running=True
        if len(manager.started)==2:
            manager.is_running=False
            raise RuntimeError('receiver transfer failed')
        return True
    monkeypatch.setattr(runtime,'start_scene',start)
    runner=PlaylistRunner(manager,owner,clock=lambda:clock[0])
    assert runner.start(command(owner,{'name':'first'}))['phase']=='running'
    clock[0]=1
    assert runner.advance()['phase']=='failed'
    assert manager.scene=={'name':'second'}
    assert len(manager.started)==2
    assert runner.advance()['phase']=='failed'


def test_manual_takeover_prevents_playlist_completion_stop(monkeypatch):
    import ipc.playlist_runtime as runtime
    manager=Manager();owner=ControllerCommandCoordinator(manager);clock=[0.0]
    monkeypatch.setattr(runtime,'normalize_managed_scene',lambda manager,scene:deepcopy(scene))
    def start(manager,scene):manager.scene=deepcopy(scene);manager.is_running=True;return True
    monkeypatch.setattr(runtime,'start_scene',start)
    runner=PlaylistRunner(manager,owner,clock=lambda:clock[0])
    assert runner.start(command(owner,{'name':'first'}))['phase']=='running'
    with owner.legacy_mutation_guard():manager.scene={'name':'manual'}
    clock[0]=5
    assert runner.advance()['phase']=='overridden'
    assert manager.is_running
    assert manager.scene=={'name':'manual'}


def test_manager_snapshots_reused_render_buffer_before_async_send():
    import threading
    import numpy as np
    from animation.core.manager import AnimationManager,PreviewLEDController
    manager=AnimationManager(PreviewLEDController(1,1),auto_start=False)
    shared=np.zeros((1,3),dtype=np.uint8)
    send_started=threading.Event();rendered_second=threading.Event();sent=[]
    class Animation:
        calls=0
        def generate_frame(self,elapsed,count):
            self.calls+=1
            shared[:]=self.calls
            if self.calls==2:
                assert send_started.wait(1)
                rendered_second.set()
            if self.calls==3:manager.is_running=False
            return shared
    def send(frame,*args):
        if not sent:
            send_started.set()
            assert rendered_second.wait(1)
        sent.append(frame.copy())
        return 0.0,0.0
    manager.current_animation=Animation()
    manager._present_frame=send
    manager._reset_run_counters()
    manager._animation_loop()
    assert sent[0].tolist()==[[1,1,1]]
    assert sent[1].tolist()==[[2,2,2]]


def test_power_off_scene_selection_becomes_next_power_on_scene_and_keeps_zero():
    from animation.core.manager import AnimationManager,PreviewLEDController
    from web.starter_looks import get_starter
    from scripts.start_server import handle_command
    manager=AnimationManager(PreviewLEDController(33,138),auto_start=False)
    manager._launch_animation_loop=lambda:None
    first=get_starter('aurora')['scene']
    second=deepcopy(first);second['look']['pace']=.77
    assert handle_command(manager,'start_scene',{'scene':first})
    assert handle_command(manager,'stop',{})
    assert handle_command(manager,'start_scene',{'scene':second,'global_settings':{'output':{'power':False,'brightness':0}}})
    owner=manager._controller_command_coordinator
    assert owner.selected_scene==second
    assert manager.output_brightness==0
    assert not manager.is_running
    assert handle_command(manager,'set_device_state',{'power':True})
    assert manager.get_scene_state()['look']['pace']==.77
    assert manager.output_brightness==0


def test_canonical_manager_reports_requested_scene_and_failure_without_restore():
    from animation.core.manager import AnimationManager,PreviewLEDController
    from web.starter_looks import get_starter
    class Controller(PreviewLEDController):
        fail=False
        def set_all_pixels(self,frame):return not self.fail
    controller=Controller(33,138)
    manager=AnimationManager(controller,auto_start=False)
    manager._launch_animation_loop=lambda:None
    scene=get_starter('aurora')['scene']
    assert manager.start_scene(scene)
    assert manager.get_current_status()['scene']['provider_mode']=='host_full_rgb'
    controller.fail=True
    next_scene=deepcopy(scene);next_scene['look']['pace']=.77
    assert manager.start_scene(next_scene) is False
    status=manager.get_current_status()
    assert status['requested_scene']==next_scene
    assert status['controller_playback']['state']=='stopped'
    assert 'transfer' in status['controller_playback']['error']


def test_finite_playlist_completion_stops_host_without_restoring_initial_scene(monkeypatch):
    import ipc.playlist_runtime as runtime
    manager=Manager();manager.scene={'name':'original'};owner=ControllerCommandCoordinator(manager);clock=[0.0]
    monkeypatch.setattr(runtime,'normalize_managed_scene',lambda manager,scene:deepcopy(scene))
    def start(manager,scene):manager.scene=deepcopy(scene);manager.is_running=True;return True
    monkeypatch.setattr(runtime,'start_scene',start)
    runner=PlaylistRunner(manager,owner,clock=lambda:clock[0])
    queued=command(owner,{'name':'first'})
    assert runner.dispatch(queued)['phase']=='running'
    assert runner.dispatch(queued)['phase']=='running'
    assert owner.state_revision==1
    clock[0]=1
    assert runner.advance()['current_index']==1
    clock[0]=2
    assert runner.advance()['phase']=='completed'
    assert not manager.is_running
    assert manager.scene=={'name':'second'}


def test_legacy_animation_command_uses_host_scene_for_current_instrument():
    from animation.core.manager import AnimationManager,PreviewLEDController
    from scripts.start_server import handle_command
    manager=AnimationManager(PreviewLEDController(33,138),auto_start=False)
    manager._launch_animation_loop=lambda:None
    assert handle_command(manager,'start',{'animation':'gradient','config':{'motion':.2}})
    assert manager.get_scene_state()['animation']['component_id']=='gradient'
    assert manager.get_current_status()['scene']['provider_mode']=='host_full_rgb'


def test_live_operator_tempo_changes_continue_scene_time_without_jump(monkeypatch):
    import numpy as np
    import animation.core.manager as manager_module
    from animation.core.compositing import BaseFrame
    manager=manager_module.AnimationManager(manager_module.PreviewLEDController(1,1),auto_start=False)
    manager._canonical_host_full_mode=True
    manager._canonical_receiver_scene=object()
    manager._canonical_receiver_runtime=object()
    manager._reset_run_counters()
    manager.start_time=0.0
    manager.animation_speed_scale=1.0
    clock=[0.0];elapsed=[]
    monkeypatch.setattr(manager_module.time,'perf_counter',lambda:clock[0])
    monkeypatch.setattr(manager_module,'_wait_for_frame_deadline',lambda deadline:None)
    def render(*args,monotonic_elapsed,**kwargs):
        elapsed.append(monotonic_elapsed)
        if len(elapsed)==1:
            manager.animation_speed_scale=2.0;clock[0]=.1
        elif len(elapsed)==2:
            manager.animation_speed_scale=.5;clock[0]=.2
        else:manager.is_running=False
        return BaseFrame(np.zeros((1,3),dtype=np.uint8),changed=True)
    manager.render_scene_v2_presentation=render
    manager._animation_loop()
    assert elapsed==[0.0,.2,.25]


def test_file_playlist_queue_polls_idle_executes_once_and_never_replays(tmp_path, monkeypatch):
    import ipc.playlist_runtime as runtime
    from scripts.start_server import process_playlist_commands
    channel=FileControlChannel(tmp_path/'control.json',tmp_path/'status.json')
    manager=Manager();owner=ControllerCommandCoordinator(manager)
    monkeypatch.setattr(runtime,'normalize_managed_scene',lambda manager,scene:deepcopy(scene))
    def start(manager,scene):manager.scene=deepcopy(scene);manager.is_running=True;manager.started.append(scene);return True
    monkeypatch.setattr(runtime,'start_scene',start)
    runner=PlaylistRunner(manager,owner,status_sink=channel.write_playlist_current_status)
    process_playlist_commands(channel,runner)
    queued=command(owner,{'name':'first'})
    channel.enqueue_playlist_command(queued)
    process_playlist_commands(channel,runner)
    assert channel.read_playlist_request_status(queued['request_id'])['phase']=='running'
    assert channel.read_playlist_current_status()['phase']=='running'
    for _ in range(3):process_playlist_commands(channel,runner)
    assert len(manager.started)==1
    reconnected=FileControlChannel(tmp_path/'control.json',tmp_path/'status.json')
    assert reconnected.poll_playlist_commands('new-session')==[]

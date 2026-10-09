#!/usr/bin/env python3
"""Run the host RGB controller or browser/preview process."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from animation.core.manager import AnimationManager
from animation.core.defaults import DEFAULT_ANIMATION_SPEED_SCALE, DEFAULT_PLANT_AWARE
from animation.core.installation_profile_topology import INSTALLED_INSTALLATION_PROFILE_TOPOLOGY
from ipc.control_channel import FileControlChannel
from ipc.playlist_runtime import PlaylistRunner, normalize_playlist_command
from ipc.runtime_control import (ControllerCommandConflictError, controller_activation_coordinator,
    start_scene as _start_scene, normalize_managed_scene, update_scene_component as _update_scene_component,
    restore_display_state as _restore_display_state)
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP, wall_device_map
from tools.deployment.preserve_deploy_settings import load_saved_state,save_status

PRODUCTION_STAGGER_PHASES=3
FEC_RECEIVER_IDS_ENV='LEDGRID_FEC_RECEIVER_IDS'

def apply_global_settings(manager, settings):
    if not settings:
        return
    if settings.get('output') is not None:
        output = settings['output']
        if 'brightness' in output:
            manager.set_output_brightness(output['brightness'])
        if 'target_fps' in output:
            manager.set_target_fps(output['target_fps'])
        if 'animation_speed_scale' in output:
            manager.set_animation_speed_scale(output['animation_speed_scale'])
    if settings.get('plant_modifiers') is not None:
        manager.set_plant_modifiers(settings['plant_modifiers'])
    if settings.get('vibe') is not None:
        manager.set_vibe(settings['vibe'])

def handle_command(manager, action, data):
    coordinator=controller_activation_coordinator(manager)
    data=dict(data)
    guard=data.pop('_controller_guard',None)
    with coordinator.legacy_mutation_guard(guard):
        if action=='start_scene':
            settings=data.get('global_settings') or {}
            apply_global_settings(manager,settings)
            if settings.get('output',{}).get('power') is False:
                requested=normalize_managed_scene(manager,data.get('scene'))
                manager._requested_scene=requested
                return manager.stop_animation()
        return _dispatch_legacy_command(manager,action,data,selected_scene=coordinator.selected_scene)

def controller_status_payload(manager, release_id=None, **_unused):
    status=manager.get_current_status()
    status.update(controller_activation_coordinator(manager).controller_status())
    if release_id is not None:
        status['release_id']=release_id
    return status

def run_controller_mode(args):
    from drivers.multi_device import MultiDeviceLEDController
    if args.strips != DEFAULT_STRIP_COUNT or args.leds_per_strip != DEFAULT_LEDS_PER_STRIP:
        raise ValueError('hardware mode requires the installed 33x138 wall')
    saved=load_saved_state(state_path=Path(args.saved_state_file),presets_dir=Path(args.presets_dir))
    controller=MultiDeviceLEDController(num_devices=5,strip_count=33,leds_per_strip=138,
        device_map=wall_device_map(5),debug=args.controller_debug,
        fec_receiver_ids=receiver_fec_ids_for_runtime(),speed=args.spi_speed)
    # Brightness zero is the startup default; an explicitly saved value is applied first.
    controller.set_brightness(saved.get('brightness',args.brightness) if saved else args.brightness)
    controller.configure()
    apply_production_stagger(controller)
    manager=AnimationManager(controller,plugins_dir=args.animations_dir,
        animation_speed_scale=saved.get('animation_speed_scale',args.animation_speed_scale) if saved else args.animation_speed_scale,
        plant_modifiers=saved.get('plant_modifiers') if saved else None,
        vibe=saved.get('vibe') if saved else None,
        auto_start=False)
    manager.set_target_fps(saved.get('target_fps',args.target_fps) if saved else args.target_fps)
    if saved and saved.get('power',True):
        try:
            if saved.get('scene'):
                if not _start_scene(manager,saved['scene']):
                    raise RuntimeError('saved Scene could not start')
            elif saved.get('animation'):
                if not manager.start_animation(saved['animation'],saved.get('params'),preset=saved.get('current_preset')):
                    raise RuntimeError('saved animation could not start')
        except (RuntimeError,TypeError,ValueError) as exc:
            manager.stop_animation(clear_leds=False)
            manager._playback_error=f'Saved playback unavailable: {exc}'
    channel=FileControlChannel(args.control_file,args.status_file)
    coordinator=controller_activation_coordinator(manager,restored_selected_scene=saved.get('scene') if saved else None)
    runner=PlaylistRunner(manager,coordinator,status_sink=channel.write_playlist_current_status)
    channel.write_playlist_current_status(runner.status())
    last_status=0.0
    last_idle_probe=0.0
    last_result=None
    last_command_id=0
    last_applied_command_id=0
    # Reject commands left queued by an earlier controller process. Commands target a live session.
    for command in channel.poll_commands():
        channel.acknowledge_command(command,{'request_id':command['request_id'],'command_id':command['command_id'],'state':'failed','error':'controller restarted before command execution'})
    try:
        while True:
            for command in channel.poll_commands():
                error=None
                try:
                    accepted=handle_command(manager,command['action'],command.get('data') or {})
                    if accepted is False:
                        error=getattr(manager,'_playback_error',None) or 'controller rejected command'
                except (RuntimeError,TypeError,ValueError) as exc:
                    error=str(exc)
                last_result={'request_id':command['request_id'],'command_id':command['command_id'],
                    'state':'failed' if error else 'completed','error':error,'completed_at':time.time()}
                channel.acknowledge_command(command,last_result)
                last_command_id=command['command_id']
                if not error:
                    last_applied_command_id=last_command_id
                save_status(controller_status_payload(manager),presets_dir=Path(args.presets_dir),state_path=Path(args.saved_state_file))
            process_playlist_commands(channel,runner)
            runner.advance()
            if time.monotonic()-last_status>=args.status_interval:
                # Playback frames already sample status. Probe while stopped so
                # connectivity does not turn stale merely because the wall is off.
                if not manager.is_running and time.monotonic()-last_idle_probe>=2:
                    refresh=getattr(controller,'refresh_receiver_status',None)
                    if callable(refresh):
                        refresh()
                    last_idle_probe=time.monotonic()
                payload=controller_status_payload(manager)
                payload['command_result']=last_result
                payload['last_command_id']=last_command_id
                payload['last_applied_command_id']=last_applied_command_id
                channel.write_status(payload)
                last_status=time.monotonic()
            time.sleep(args.poll_interval)
    finally:
        manager.stop_animation(clear_leds=False)
        controller.close()

def run_web_mode(args):
    from web.app import create_app
    channel=FileControlChannel(args.control_file,args.status_file)
    interface=create_app(control_channel=channel,host=args.host,port=args.port,
        strips=args.strips,leds_per_strip=args.leds_per_strip,animations_dir=args.animations_dir,
        animation_speed_scale=args.animation_speed_scale)
    interface.run(debug=args.debug)

def main():
    root=Path(__file__).resolve().parents[1]
    data_root=Path(os.environ.get('LEDGRID_DATA_DIR',str(root)))
    parser=argparse.ArgumentParser(description='Host RGB wall server')
    parser.add_argument('--mode',choices=['controller','web'],default='web')
    parser.add_argument('--animations-dir',default=str(root/'animation/plugins'))
    parser.add_argument('--control-file',default=str(data_root/'run_state/control.json'))
    parser.add_argument('--status-file',default=str(data_root/'run_state/status.json'))
    parser.add_argument('--presets-dir',default=str(data_root/'presets/animations'))
    parser.add_argument('--saved-state-file',default=str(data_root/'run_state/before_deploy.json'))
    parser.add_argument('--strips',type=int,default=33)
    parser.add_argument('--leds-per-strip',type=int,default=138)
    parser.add_argument('--host',default='0.0.0.0')
    parser.add_argument('--port',type=int,default=5000)
    parser.add_argument('--debug',action='store_true')
    parser.add_argument('--controller-debug',action='store_true')
    parser.add_argument('--bus',type=int,default=0)
    parser.add_argument('--device',type=int,default=0)
    parser.add_argument('--spi-speed',type=int,default=20000000)
    parser.add_argument('--target-fps',type=int,default=200)
    parser.add_argument('--brightness',type=int,default=0)
    parser.add_argument('--animation-speed-scale',type=float,default=DEFAULT_ANIMATION_SPEED_SCALE)
    parser.add_argument('--poll-interval',type=float,default=.05)
    parser.add_argument('--status-interval',type=float,default=.5)
    args=parser.parse_args()
    (run_controller_mode if args.mode=='controller' else run_web_mode)(args)

def apply_production_stagger(controller, phases: int = PRODUCTION_STAGGER_PHASES) -> bool:
    """Enable WS2812 edge staggering on a live controller. Safe no-op on mocks."""
    if not hasattr(controller, "set_stagger_phases"):
        return False
    controller.set_stagger_phases(phases)
    return True


def receiver_fec_ids_for_runtime(value: str | None = None) -> tuple[int, ...]:
    """Parse an explicit, fail-closed logical-receiver FEC allowlist."""
    if value is None:
        value = os.environ.get(FEC_RECEIVER_IDS_ENV, "3")
    if not isinstance(value, str):
        raise TypeError(f"{FEC_RECEIVER_IDS_ENV} must be text")
    if value == "":
        return ()
    tokens = value.split(",")
    if any(not token.isdecimal() or str(int(token)) != token for token in tokens):
        raise ValueError(
            f"{FEC_RECEIVER_IDS_ENV} must be canonical comma-separated logical IDs"
        )
    receiver_ids = tuple(int(token) for token in tokens)
    if len(set(receiver_ids)) != len(receiver_ids):
        raise ValueError(f"{FEC_RECEIVER_IDS_ENV} cannot contain duplicate IDs")
    return receiver_ids


def process_playlist_commands(channel, runner: PlaylistRunner) -> int:
    """Execute immutable playlist intents once for the current controller."""
    completed = 0
    for raw in channel.poll_playlist_commands(runner.coordinator.session_id):
        request_id = raw.get("request_id")
        try:
            command = normalize_playlist_command(raw)
            status = runner.dispatch(command)
        except (TypeError, ValueError, RuntimeError) as exc:
            status = {
                "schema": "ledgrid.playlist-status", "schema_version": 1,
                "phase": "rejected", "controller_session_id": runner.coordinator.session_id,
                "updated_at": time.time(), "run_id": raw.get("run_id"),
                "request_id": request_id, "playlist_id": raw.get("playlist_id"),
                "playlist_name": raw.get("playlist_name"), "current_index": None,
                "entry_count": 0, "current_entry": None, "entry_started_at": None,
                "entry_deadline_at": None, "remaining_seconds": None, "error": str(exc),
            }
        channel.write_playlist_request_status(status)
        channel.acknowledge_playlist_poll(request_id)
        completed += 1
    return completed


def _dispatch_legacy_command(
    manager: AnimationManager,
    action: str,
    data: dict,
    *,
    selected_scene: dict | None = None,
):
    """Dispatch a command and report whether restart state changed."""
    if action == 'start':
        animation = data.get('animation')
        config = data.get('config') or {}
        print(f"▶️  Start requested: {animation}")
        return manager.start_animation(animation, config, preset=data.get('preset'))
    elif action == 'start_scene':
        print("▶️  Scene start requested")
        try:
            return _start_scene(manager, data.get("scene"))
        except (TypeError, ValueError) as exc:
            print(f"⚠️ Invalid scene: {exc}")
            return False
    elif action == 'update_scene_component':
        try:
            return _update_scene_component(
                manager, data.get("target"), data.get("update") or {}
            )
        except (TypeError, ValueError) as exc:
            print(f"⚠️ Invalid scene update: {exc}")
            return False
    elif action == 'stop_scene':
        return manager.stop_animation()
    elif action == 'restore_display_state':
        try:
            return _restore_display_state(manager, data.get("state"))
        except (RuntimeError, TypeError, ValueError) as exc:
            print(f"⚠️ Invalid desired display state: {exc}")
            return False
    elif action == 'stop':
        print("⏹️  Stop requested")
        return manager.stop_animation()
    elif action == 'update_params':
        params = data.get('params') or {}
        if params:
            print(f"⚙️  Update params: {params}")
            return manager.update_animation_parameters(params)
    elif action == 'set_current_preset':
        preset = data.get('preset') or {}
        return manager.set_current_preset(preset)
    elif action == 'set_target_fps':
        requested = data.get('target_fps')
        try:
            applied = manager.set_target_fps(int(requested))
            print(f"🎚️ Target FPS: {applied}")
            return True
        except (TypeError, ValueError):
            print(f"⚠️ Invalid target FPS: {requested!r}")
            return False
    elif action == 'set_animation_speed_scale':
        requested = data.get('animation_speed_scale')
        try:
            applied = manager.set_animation_speed_scale(float(requested))
            print(f"🎚️ Animation speed scale: {applied:.3f}")
            return True
        except (TypeError, ValueError):
            print(f"⚠️ Invalid animation speed scale: {requested!r}")
            return False
    elif action == 'set_output_brightness':
        requested = data.get('brightness')
        try:
            applied = manager.set_output_brightness(requested)
            print(f"💡 Output brightness: {applied}")
            return True
        except (RuntimeError, TypeError, ValueError):
            print(f"⚠️ Invalid output brightness: {requested!r}")
            return False
    elif action == 'set_device_state':
        try:
            if (
                data == {"power": True}
                and not manager.is_running
                and selected_scene is not None
            ):
                applied = _start_scene(manager, selected_scene)
            else:
                applied = manager.apply_device_state(data)
            print(f"🏛️ Device state: {data}")
            return bool(applied)
        except (RuntimeError, TypeError, ValueError) as exc:
            print(f"⚠️ Invalid device state: {exc}")
            return False
    elif action == 'set_plant_aware':
        requested = data.get('plant_aware')
        try:
            applied = manager.set_plant_aware(requested)
            print(f"🌿 Plant-aware mode: {'on' if applied else 'off'}")
            return True
        except (TypeError, ValueError):
            print(f"⚠️ Invalid plant-aware state: {requested!r}")
            return False
    elif action == 'set_plant_modifiers':
        requested = data.get('plant_modifiers')
        try:
            applied = manager.set_plant_modifiers(requested)
            print(f"🌿 Plant modifiers: {', '.join(applied['active']) or 'off'}")
            return True
        except (TypeError, ValueError):
            print(f"⚠️ Invalid plant modifier state: {requested!r}")
            return False
    elif action == 'set_vibe':
        requested = data.get('vibe', data.get('vibe_id'))
        if requested is None:
            print("⚠️ Invalid vibe: None")
            return False
        try:
            applied = manager.set_vibe(requested)
            state = applied.get('state', applied) if isinstance(applied, dict) else {}
            vibe_id = state.get('id', state.get('vibe_id', requested))
            print(f"🎨 Vibe: {vibe_id}")
            return True
        except (TypeError, ValueError):
            print(f"⚠️ Invalid vibe: {requested!r}")
            return False
    elif action == 'refresh_receiver_status':
        request_id = data.get('request_id')
        refresher = getattr(manager.controller, 'refresh_receiver_status', None)
        if not callable(refresher):
            print("⚠️ Receiver status refresh is unavailable")
            return False
        try:
            refresher(request_id)
            print("📡 Receiver connectivity refreshed")
            return True
        except (RuntimeError, TypeError, ValueError) as exc:
            print(f"⚠️ Receiver status refresh rejected: {exc}")
        return False
    elif action == 'refresh_plugins':
        animation = data.get('animation')
        if animation:
            print(f"🔄 Reload plugin: {animation}")
            manager.reload_animation(animation)
        else:
            print("🔄 Refresh all plugins")
            manager.refresh_plugins()
    elif action == 'puncture_hole':
        x = data.get('x')
        y = data.get('y')
        radius = data.get('radius')
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            print(f"💥 Hole requested at ({x:.1f}, {y:.1f})")
            manager.trigger_hole(float(x), float(y), radius)
        else:
            print("💥 Random hole requested")
            manager.trigger_random_hole()
    elif action == 'animation_interaction':
        try:
            return manager.dispatch_interaction(
                data.get('kind', 'primary'), data.get('x'), data.get('y'),
                data.get('strength', 1.0),
            )
        except (TypeError, ValueError) as exc:
            print(f"⚠️ Invalid animation interaction: {exc}")
            return False
    elif action == 'dpad':
        direction = (data.get('direction') or '').lower().replace('_', '-')
        if manager.current_animation and hasattr(manager.current_animation, 'handle_input'):
            manager.current_animation.handle_input(direction)
        else:
            print(f"⚠️ D-pad input ignored (no handler): {direction}")
    else:
        raise ValueError(f'Unknown action: {action}')
    return True


if __name__=='__main__':
    main()

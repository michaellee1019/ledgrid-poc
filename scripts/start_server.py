#!/usr/bin/env python3
"""Run the host RGB controller or browser/preview process."""
import argparse
import logging
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
from ipc.controller_frame_snapshot import ControllerFrameSnapshot
from ipc.playlist_runtime import PlaylistRunner, normalize_playlist_command
from ipc.runtime_control import (ControllerCommandConflictError, controller_activation_coordinator,
    start_scene as _start_scene, normalize_managed_scene, update_scene_component as _update_scene_component,
    restore_display_state as _restore_display_state, normalize_automation_device_state)
from drivers.led_layout import (
    DEFAULT_LEDS_PER_STRIP, default_strip_count, hardware_receiver_count,
    hardware_device_map,
)
from tools.deployment.preserve_deploy_settings import load_saved_state,save_status

PRODUCTION_STAGGER_PHASES=3
logger = logging.getLogger(__name__)

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

def handle_command(manager, action, data, *, gamepad=None):
    coordinator=controller_activation_coordinator(manager)
    data=dict(data)
    guard=data.pop('_controller_guard',None)
    if action == 'start_tetris_player':
        if guard is None or data:
            raise ValueError('Tetris play requires a guarded request')
        if gamepad is None or not gamepad.connected:
            raise ValueError('Turn on the Nimbus and wait for it to connect before starting Tetris.')
    with coordinator.legacy_mutation_guard(guard):
        if action == 'start_tetris_player':
            from web.starter_looks import tetris_player_scene

            return manager.start_canonical_scene(tetris_player_scene(), tetris_player=True)
        if action == 'tune_scene':
            return manager.tune_canonical_scene(data['scene'])
        if action == 'automation_interaction':
            return manager.dispatch_canonical_interaction(**data)
        if action == 'automation_device_state':
            from animation.core.defaults import DEFAULT_ANIMATION_SPEED_SCALE
            state = normalize_automation_device_state(data)
            selected = coordinator.selected_scene
            if state.get('power') is True and not manager.is_running:
                if selected is None:
                    raise ValueError('No selected scene is available; choose content before powering on.')
                # Validate the selected scene before changing any settings.
                selected = normalize_managed_scene(manager, selected)
            if 'brightness' in state:
                manager.set_output_brightness(state['brightness'])
            if 'pace' in state:
                manager.set_animation_speed_scale(state['pace'] * DEFAULT_ANIMATION_SPEED_SCALE)
            if state.get('power') is False:
                return manager.stop_animation()
            if state.get('power') is True and not manager.is_running:
                return _start_scene(manager, selected)
            return True
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

def process_control_command(channel, manager, command, *, last_applied_command_id=0, gamepad=None):
    """Execute once and publish current state before exposing its receipt."""
    coordinator = controller_activation_coordinator(manager)
    error = None
    try:
        accepted = handle_command(manager, command['action'], command.get('data') or {}, gamepad=gamepad)
        if accepted is False:
            error = getattr(manager, '_playback_error', None) or 'controller rejected command'
    except (RuntimeError, TypeError, ValueError) as exc:
        error = str(exc)
    result = {'request_id': command['request_id'], 'command_id': command['command_id'],
              'state': 'failed' if error else 'completed', 'error': error, 'completed_at': time.time(),
              'controller_session_id': coordinator.session_id,
              'controller_state_revision': coordinator.state_revision}
    status = controller_status_payload(manager)
    status.update(command_result=result, last_command_id=command['command_id'],
                  last_applied_command_id=last_applied_command_id if error else command['command_id'])
    channel.write_status(status)
    channel.acknowledge_command(command, result)
    return result


def nimbus_command(manager, coordinator, gamepad, action, data):
    """Run a local intent at the same revision boundary as browser commands."""
    guard = {
        'expected_controller_session_id': coordinator.session_id,
        'expected_controller_state_revision': coordinator.state_revision,
        'expires_at': time.time() + 5,
    }
    return handle_command(manager, action, {**data, '_controller_guard': guard}, gamepad=gamepad)


def poll_wall_composer(manager, gamepad, menu):
    from drivers.nimbus_gamepad import StickBank, StickMotion

    game, player_state = manager.nimbus_game()
    active = manager.is_running and manager.output_brightness > 0
    player = player_state == "playing" and not menu.is_open
    target = game if player else menu
    tuning = not player and active
    browsing = not menu.is_open and not player
    track_bank = active and (menu.is_open or not player)
    buttons = gamepad.poll(target, navigating=menu.is_open,
                           tuning_sticks=tuning, browsing=browsing, track_bank=track_bank,
                           enter_player=browsing and active and game is not None,
                           steering_releases=player and manager.current_animation_name == "canopy_cup")
    menu.shoulder_bank = gamepad.shoulder_bank
    motions = []
    for button in buttons:
        if isinstance(button, StickBank):
            menu.shoulder_bank = button.bank
            if track_bank:
                was_momentary = menu.momentary
                menu.select_stick_bank(button.bank)
                if was_momentary and not menu.is_open:
                    gamepad.handoff(target)
                    return
            continue
        if isinstance(button, StickMotion):
            motions.append(button)
            continue
        if button == 'disconnect':
            manager.dispatch_game_input(game, 'disconnect')
            menu.shoulder_bank = 0
            menu.close()
            return
        if button == 'menu':
            if menu.is_open and not menu.momentary:
                menu.close()
            else:
                manager.dispatch_game_input(game, 'pause')
                menu.open()
                try:
                    if not manager.is_running and menu.owner.selected_scene is not None:
                        menu._run('automation_device_state', {'power': True})
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    menu._feedback('Unavailable', str(exc), error=exc)
                try:
                    if manager.output_brightness == 0:
                        menu._run('set_output_brightness', {'brightness': 16})
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    menu._feedback('Wall light', str(exc), error=exc)
            gamepad.handoff(menu)
            return
        if menu.is_open:
            playback_revision = menu.playback_revision
            menu.press(button)
            if not menu.is_open:
                game, state = manager.nimbus_game()
                gamepad.handoff(game if state == "playing" else menu)
                return
            if menu.playback_revision != playback_revision:
                gamepad.discard_pending()
                return
        elif player and (active or button.startswith("release-")):
            manager.dispatch_game_input(game, button)
        elif browsing and active and button == "a" and game is not None:
            if menu.play_game(game, player_state):
                gamepad.handoff(game)
                return
        elif not player and active and button in ('up', 'down', 'left', 'right'):
            if menu.quick_browse(button):
                gamepad.discard_pending()
                return
    if tuning and motions:
        menu.tune_sticks(motions)


def reconcile_wall_menu(manager, gamepad, menu, prior_revision, *, scheduled=False):
    """Discard controls queued against the outgoing scene or menu page."""
    if menu.owner.state_revision == prior_revision:
        return False
    if scheduled:
        menu.refresh_scene()
    else:
        menu.close()
        manager.set_wall_menu_pixels(None)
    gamepad.discard_pending()
    return True


def run_controller_mode(args):
    from drivers.multi_device import MultiDeviceLEDController
    from drivers.nimbus_gamepad import NimbusGamepad
    from animation.core.wall_menu_frame import WallMenuFrame
    from web.wall_composer import WallComposer
    from functools import partial
    num_devices = hardware_receiver_count(args.strips, args.leds_per_strip)
    saved=load_saved_state(state_path=Path(args.saved_state_file),presets_dir=Path(args.presets_dir))
    controller=MultiDeviceLEDController(num_devices=num_devices,strip_count=args.strips,
        leds_per_strip=args.leds_per_strip,device_map=hardware_device_map(num_devices),
        debug=args.controller_debug,speed=args.spi_speed)
    controller.configure()
    apply_production_stagger(controller)
    manager=AnimationManager(controller,plugins_dir=args.animations_dir,
        animation_speed_scale=saved.get('animation_speed_scale',args.animation_speed_scale) if saved else args.animation_speed_scale,
        plant_modifiers=saved.get('plant_modifiers') if saved else None,
        vibe=saved.get('vibe') if saved else None,
        auto_start=False)
    # Brightness must run after stagger so the queue is not full of CONFIG/mask
    # traffic; firmware keeps brightness=0 until SET_BRIGHTNESS is accepted.
    startup_brightness = saved.get('brightness', args.brightness) if saved else args.brightness
    manager.set_output_brightness(startup_brightness)
    refresh = getattr(controller, 'refresh_receiver_status', None)
    if callable(refresh):
        refresh()
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
    frame_snapshot=ControllerFrameSnapshot(channel,manager)
    coordinator=controller_activation_coordinator(manager,restored_selected_scene=saved.get('scene') if saved else None)
    runner=PlaylistRunner(manager,coordinator,status_sink=channel.write_playlist_current_status)
    gamepad = NimbusGamepad()
    project_root = Path(__file__).resolve().parents[1]
    menu = WallComposer(manager, coordinator, runner,
                        partial(nimbus_command, manager, coordinator, gamepad), project_root,
                        Path(args.control_file).parent, Path(args.presets_dir))
    menu_frame = None
    saved_state_path = Path(args.saved_state_file)
    presets_dir = Path(args.presets_dir)
    channel.write_playlist_current_status(runner.status())
    last_status=0.0
    nimbus_scene_revision = None
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
                revision = coordinator.state_revision
                last_result = process_control_command(channel, manager, command,
                                                      last_applied_command_id=last_applied_command_id,
                                                      gamepad=gamepad)
                last_command_id = command['command_id']
                if last_result['state'] == 'completed':
                    last_applied_command_id = last_command_id
                if command['action'] != 'refresh_receiver_status':
                    if reconcile_wall_menu(manager, gamepad, menu, revision):
                        nimbus_scene_revision = None
                elif nimbus_scene_revision is not None:
                    nimbus_scene_revision = coordinator.state_revision
                save_status(controller_status_payload(manager), presets_dir=presets_dir, state_path=saved_state_path)
            revision = coordinator.state_revision
            process_playlist_commands(channel,runner)
            if reconcile_wall_menu(manager, gamepad, menu, revision):
                nimbus_scene_revision = None
            revision = coordinator.state_revision
            runner.advance()
            if reconcile_wall_menu(manager, gamepad, menu, revision, scheduled=True):
                if nimbus_scene_revision is not None:
                    nimbus_scene_revision = coordinator.state_revision
            revision = coordinator.state_revision
            scene_before = manager.current_animation_hash
            poll_wall_composer(manager, gamepad, menu)
            if coordinator.state_revision != revision and manager.get_scene_state() is not None:
                if nimbus_scene_revision is not None or scene_before != manager.current_animation_hash:
                    nimbus_scene_revision = coordinator.state_revision
            try:
                sections = menu.hud_sections()
                if sections and menu_frame is None:
                    menu_frame = WallMenuFrame()
                pixels = menu_frame.render_sections(sections) if sections else None
                manager.set_wall_menu_pixels(pixels)
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
                logger.warning('Wall menu presentation failed: %s', exc)
                menu.error = str(exc)
                menu.close()
            if coordinator.state_revision != revision:
                try:
                    save_status(controller_status_payload(manager), presets_dir=presets_dir, state_path=saved_state_path)
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    logger.warning('Could not save Nimbus playback settings: %s', exc)
                    menu.error = str(exc)
            frame_snapshot.poll()
            if time.monotonic()-last_status>=args.status_interval:
                # Playback frames already sample status. Probe while stopped so
                # connectivity does not turn stale merely because the wall is off.
                if not manager.is_running and time.monotonic()-last_idle_probe>=2:
                    refresh=getattr(controller,'refresh_receiver_status',None)
                    if callable(refresh):
                        refresh()
                    last_idle_probe=time.monotonic()
                payload=controller_status_payload(manager)
                payload['nimbus'] = {
                    'connected': gamepad.connected, 'menu_open': menu.is_open,
                    'error': menu.error, 'scene_revision': nimbus_scene_revision,
                    'saved_revision': menu.saved_revision,
                }
                payload['command_result']=last_result
                payload['last_command_id']=last_command_id
                payload['last_applied_command_id']=last_applied_command_id
                channel.write_status(payload)
                last_status=time.monotonic()
            time.sleep(args.poll_interval)
    finally:
        gamepad.close()
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
    parser.add_argument('--strips',type=int,default=default_strip_count())
    parser.add_argument('--leds-per-strip',type=int,default=DEFAULT_LEDS_PER_STRIP)
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
        status['controller_state_revision'] = runner.coordinator.state_revision
        previous = channel.read_status() or {}
        current = (controller_status_payload(runner.manager)
                   if callable(getattr(runner.manager, 'get_current_status', None))
                   else runner.coordinator.controller_status())
        current.update({key: previous.get(key) for key in ('command_result', 'last_command_id', 'last_applied_command_id')})
        channel.write_status(current)
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

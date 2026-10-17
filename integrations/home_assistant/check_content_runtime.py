"""Native HA content + real HTTP/controller and optional Composer browser smoke.

Run with the shared isolated HA runtime; all state and commands stay on loopback.
"""
import argparse
import asyncio
import os
from pathlib import Path
import tempfile
import sys
import uuid

from aiohttp import web
from homeassistant.helpers import entity_registry as er

from check_native_runtime import native_hass, serve, setup_flow

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


async def main(browser=False):
    from animation.core.manager import AnimationManager, PreviewLEDController
    from ipc.control_channel import FileControlChannel
    from ipc.playlist_runtime import PlaylistRunner
    from scripts.start_server import controller_status_payload, handle_command, process_control_command, process_playlist_commands
    from web.app import AnimationWebInterface
    from web.starter_looks import get_starter

    with tempfile.TemporaryDirectory(prefix='ledgrid-content-') as fixture_dir:
        fixture = Path(fixture_dir)
        os.environ['LEDGRID_DATA_DIR'] = str(fixture)
        manager = AnimationManager(PreviewLEDController(33, 138), auto_start=False)
        manager._launch_animation_loop = lambda: None
        handle_command(manager, 'start_scene', {'scene': get_starter('aurora')['scene']})
        manager.set_output_brightness(0)
        channel = FileControlChannel(fixture / 'control.json', fixture / 'status.json')
        channel.write_status(controller_status_payload(manager))
        interface = AnimationWebInterface(channel, manager, project_root=fixture)
        interface.composer_presets.root = ROOT
        interface._composer_submit_scene(get_starter('aurora')['scene'], client_id='fixture')
        looks = []
        for name, starter in [('First saved scene', 'aurora_clock'), ('Second saved scene', 'aurora_conway')]:
            canonical = interface._composer_canonical({'origin': 'composer', 'scene': get_starter(starter)['scene']})
            looks.append(interface.composer_looks.save_as(name, canonical))
        saved_playlist = interface.composer_playlists.save({'name': 'Runtime playlist', 'entries': [
            {'entry_id': str(uuid.uuid4()), 'label': look['name'], 'duration_seconds': 60, 'scene': look['scene']} for look in looks]})
        playlist = PlaylistRunner(manager, status_sink=channel.write_playlist_current_status)
        channel.write_playlist_current_status(playlist.status())
        commands = []
        running = True
        async def pump():
            last_id = last_applied = 0
            while running:
                for command in channel.poll_commands():
                    commands.append(command)
                    result = process_control_command(channel, manager, command, last_applied_command_id=last_applied)
                    last_id = command['command_id']
                    if result['state'] == 'completed':
                        last_applied = last_id
                before = len(channel.poll_playlist_commands(playlist.coordinator.session_id))
                process_playlist_commands(channel, playlist)
                commands.extend(['playlist'] * before)
                playlist.advance()
                channel.write_status({**controller_status_payload(manager), 'last_command_id': last_id, 'last_applied_command_id': last_applied})
                await asyncio.sleep(.025)
        async def proxy(request):
            data = await request.read()
            def dispatch():
                with interface.app.test_client() as client:
                    response = client.open(request.path_qs, method=request.method, data=data, content_type=request.content_type)
                    return response.status_code, response.get_data(), response.content_type
            status, data, content_type = await asyncio.to_thread(dispatch)
            return web.Response(status=status, body=data, headers={'Content-Type': content_type})
        app = web.Application()
        app.router.add_route('*', '/{path:.*}', proxy)
        server, url = await serve(app)
        controller = asyncio.create_task(pump())
        with tempfile.TemporaryDirectory(prefix='ledgrid-content-ha-') as config_dir:
            hass = await native_hass(config_dir)
            try:
                result = await setup_flow(hass, url)
                assert result['type'] == 'create_entry', result
                entry = result['result']
                coord = entry.runtime_data
                assert commands == []
                registry = er.async_get(hass)
                grouped = er.async_entries_for_config_entry(registry, entry.entry_id)
                assert len(grouped) >= 9 and len({item.device_id for item in grouped}) == 1
                assert len({item.unique_id for item in grouped}) == len(grouped)
                animation = hass.states.get('select.led_grid_wall_animation')
                assert len(animation.attributes['options']) == 42
                selected = next(item for item in coord.catalog['content'] if item['id'] == 'animation:python:gradient:v1')
                await hass.services.async_call('select', 'select_option', {'entity_id': animation.entity_id, 'option': selected['label']}, blocking=True)
                assert manager.get_scene_state()['animation']['component_id'] == 'gradient'
                assert manager.output_brightness == 0
                assert hass.states.get(animation.entity_id).state == selected['label']
                assert interface.working_draft.get()['scene'] == manager.get_scene_state()
                preset = next(item for item in coord.catalog['content'] if item['kind'] == 'preset' and item.get('component_id') == 'gradient')
                await hass.services.async_call('select', 'select_option', {'entity_id': 'select.led_grid_wall_preset', 'option': preset['label']}, blocking=True)
                assert hass.states.get('select.led_grid_wall_preset').state == preset['label']
                assert manager.output_brightness == 0
                receipt = await hass.services.async_call('led_grid_wall', 'play_content', {'content_id': 'look:' + looks[0]['id']}, blocking=True, return_response=True)
                assert receipt['success'], receipt
                assert manager.get_scene_state() == looks[0]['scene']
                assert hass.states.get('select.led_grid_wall_look').attributes['content_id'] == 'look:' + looks[0]['id']
                receipt = await hass.services.async_call('led_grid_wall', 'play_content', {'content_id': 'playlist:' + saved_playlist['id']}, blocking=True, return_response=True)
                assert receipt['success'], receipt
                assert playlist.status()['phase'] == 'running'
                retained = manager.get_scene_state()
                await hass.services.async_call('button', 'press', {'entity_id': 'button.led_grid_wall_stop_playlist'}, blocking=True)
                assert playlist.status()['phase'] == 'stopped' and manager.is_running
                assert manager.get_scene_state() == retained and manager.output_brightness == 0
                count = len(commands)
                await hass.services.async_call('button', 'press', {'entity_id': 'button.led_grid_wall_refresh_catalog'}, blocking=True)
                assert len(commands) == count
                interface.composer_looks.rename(looks[0]['id'], 'Renamed in Composer')
                await coord.async_refresh_catalog()
                assert any('Renamed in Composer' in value for value in hass.states.get('select.led_grid_wall_look').attributes['options'])
                interface.composer_looks.delete(looks[1]['id'])
                receipt = await hass.services.async_call('led_grid_wall', 'play_content', {'content_id': 'look:' + looks[1]['id']}, blocking=True, return_response=True)
                assert not receipt['success']
                assert len(commands) == count
                if browser:
                    # The explicit marker lets the loopback browser finish
                    # hydration before HA changes the current content.
                    marker = fixture / 'browser-ready'
                    process = await asyncio.create_subprocess_exec('node', str(ROOT / 'tools/browser_qualification/automation_content_probe.mjs'), url, str(marker))
                    while not marker.exists() and process.returncode is None:
                        await asyncio.sleep(.05)
                    receipt = await hass.services.async_call('led_grid_wall', 'play_content', {'content_id': 'animation:python:rainbow:v1'}, blocking=True, return_response=True)
                    assert receipt['success'], receipt
                    assert await process.wait() == 0
                    await asyncio.sleep(2.2)
                    await hass.async_block_till_done()
                    await coord.async_refresh()
                    assert (coord.data['active_content'] or {})['animation'] == 'animation:python:gradient:v1', coord.data['active_content']
                count = len(commands)
                await hass.config_entries.async_reload(entry.entry_id)
                await hass.async_block_till_done()
                assert len(commands) == count
                print('Native content runtime: 42 animations, shared device, observed selectors, Look/playlist play+stop, master zero, rename/delete, inert refresh and reload/no replay passed', flush=True)
            finally:
                await hass.async_stop()
        running = False
        await controller
        await server.cleanup()
        manager.stop_animation()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', action='store_true')
    asyncio.run(main(parser.parse_args().browser))

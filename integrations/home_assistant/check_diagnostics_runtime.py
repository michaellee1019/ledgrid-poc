"""Actual HA2026.10.1 diagnostics/image lifecycle on loopback, never hardware.

uv run --python 3.14 --with homeassistant==2026.10.1 --with pyyaml python \
    integrations/home_assistant/check_diagnostics_runtime.py
"""
import asyncio
import struct
import tempfile
import time
import zlib

from aiohttp import web
from homeassistant.components.image import async_get_image
from homeassistant.helpers import entity_registry as er

from check_native_runtime import LoopbackWall, native_hass, serve, setup_flow

DOMAIN = 'led_grid_wall'


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 33, 138, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\x00' + b'\x10\x20\x30' * 33) * 138)) + chunk(b'IEND', b''))


async def run():
    wall = LoopbackWall()
    wall.state['controller_playback'] = {'state': 'running', 'error': None}
    wall.state['receiver_connectivity'] = [{'device_index': index, 'connected': True} for index in range(5)]
    wall.state['active_content'] = {'animation': 'animation:python:aurora_curtains:v1', 'preset': None, 'look': None, 'playlist': None}
    diag_calls, frame_calls = [], []
    mode = {'diag_failure': False, 'stale_receiver': False, 'stale_controller': False}
    diagnostic = {
        'schema': 'ledgrid.automation-diagnostics', 'schema_version': 1,
        'installation_id': wall.identity, 'controller_session_id': wall.state['controller_session_id'],
        'observed_at': time.time(),
        'controller': {'connected': True, 'host_actual_fps': 149.5, 'host_target_fps': 150},
        'receivers': [{'receiver_id': index, 'connected': True, 'fresh': True,
                       'protected_status': 'verified_with_rejections' if index == 3 else 'verified',
                       'output_fps': 30.0 if index == 3 else 148.0, 'output_rate_reason': 'valid',
                       'counters': {'crc_errors': 17 if index == 3 else 0,
                                    'fec_corrected_packets': 22 if index == 3 else 0,
                                    'fec_uncorrectable_packets': 2 if index == 3 else 0,
                                    'status_integrity_errors': 4 if index == 3 else 0},
                       'observed_at': time.time()} for index in range(5)],
    }
    async def diagnostics(request):
        diag_calls.append(time.monotonic())
        if mode['diag_failure']:
            raise web.HTTPServiceUnavailable()
        diagnostic['observed_at'] = time.time() - (6 if mode['stale_controller'] else 0)
        for receiver in diagnostic['receivers']:
            receiver['observed_at'] = time.time() - (11 if mode['stale_receiver'] and receiver['receiver_id'] == 3 else 0)
        return web.json_response(diagnostic)
    async def frame(request):
        frame_calls.append(time.monotonic())
        response = web.StreamResponse(headers={
            'Content-Type': 'image/png',
            'X-LEDGrid-Captured-At': str(time.time()),
            'X-LEDGrid-Controller-Session': wall.state['controller_session_id'],
        })
        await response.prepare(request)
        await response.write(png()[:24])
        await asyncio.sleep(.02)
        await response.write(png()[24:])
        await response.write_eof()
        return response
    app = wall.app()
    app.router.add_get('/api/v1/automation/diagnostics', diagnostics)
    app.router.add_get('/api/v1/automation/frame.png', frame)
    runner, url = await serve(app)
    with tempfile.TemporaryDirectory(prefix='ledgrid-diagnostics-ha-') as config_dir:
        hass = await native_hass(config_dir)
        try:
            setup = await setup_flow(hass, url)
            assert setup['type'] == 'create_entry', setup
            entry = setup['result']
            await hass.async_block_till_done()
            registry = er.async_get(hass)
            entries = er.async_entries_for_config_entry(registry, entry.entry_id)
            detailed = [item for item in entries if any(key in item.unique_id for key in ('_host_', '_output_fps', '_crc_errors', '_protected_status', '_controller_output'))]
            assert len({item.device_id for item in entries}) == 1
            assert registry.async_get('image.led_grid_wall_controller_output') is not None
            assert detailed and all(item.disabled_by == er.RegistryEntryDisabler.INTEGRATION for item in detailed)
            assert frame_calls == [] and diag_calls == [] and wall.writes == []
            assert hass.states.get('sensor.led_grid_wall_controller_playback').state == 'running'
            assert hass.states.get('sensor.led_grid_wall_active_content').state == 'animation:python:aurora_curtains:v1'
            # Status updates change neither timestamp-only entities nor diagnostics.
            state = hass.states.get('sensor.led_grid_wall_controller_playback')
            await entry.runtime_data.async_refresh()
            assert hass.states.get(state.entity_id).last_updated == state.last_updated
            assert frame_calls == [] and diag_calls == []
            wall.state['controller_playback'] = {'state': 'stopped', 'error': 'transport failed'}
            await entry.runtime_data.async_refresh()
            assert hass.states.get(state.entity_id).state == 'failed'
            assert hass.states.get('sensor.led_grid_wall_controller_error').state == 'transport failed'
            wall.state['controller_playback'] = {'state': 'stopped', 'error': None}
            await entry.runtime_data.async_refresh()
            assert hass.states.get(state.entity_id).state == 'stopped'
            enable_keys = ('_host_actual_fps', '_receiver_3_output_fps', '_receiver_3_crc_errors', '_controller_output')
            for item in entries:
                if any(item.unique_id.endswith(key) for key in enable_keys):
                    registry.async_update_entity(item.entity_id, disabled_by=None)
            await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert len(diag_calls) == 1, diag_calls
            await asyncio.sleep(2.2)
            assert len(diag_calls) == 1
            assert entry.runtime_data.diagnostics.update_interval.total_seconds() == 30
            assert frame_calls == []
            host = hass.states.get('sensor.led_grid_wall_host_actual_cadence')
            receiver = hass.states.get('sensor.led_grid_wall_receiver_3_measured_output_cadence')
            assert host.state == '149.5' and receiver.state == '30.0', (host, receiver)
            before = host.last_updated
            diag = entry.runtime_data.diagnostics
            await diag.async_refresh()
            assert len(diag_calls) == 2 and hass.states.get(host.entity_id).last_updated == before
            mode['stale_receiver'] = True
            await diag.async_refresh()
            assert hass.states.get(receiver.entity_id).state == 'unknown'
            assert hass.states.get(host.entity_id).state == '149.5'
            mode['stale_receiver'] = False
            mode['stale_controller'] = True
            await diag.async_refresh()
            assert hass.states.get(host.entity_id).state == 'unknown'
            mode['stale_controller'] = False
            mode['diag_failure'] = True
            await diag.async_refresh()
            assert hass.states.get(host.entity_id).state == 'unavailable'
            assert hass.states.get('light.led_grid_wall').state == 'on'
            mode['diag_failure'] = False
            await diag.async_refresh()
            image = await async_get_image(hass, 'image.led_grid_wall_controller_output')
            assert image.content_type == 'image/png' and image.content == png()
            await async_get_image(hass, 'image.led_grid_wall_controller_output')
            assert len(frame_calls) == 1
            wall.mode = 'unavailable'
            await entry.runtime_data.async_refresh()
            assert hass.states.get('sensor.led_grid_wall_controller_connectivity').state == 'offline'
            assert hass.states.get('light.led_grid_wall').state == 'unavailable'
            assert hass.states.get(host.entity_id).state == 'unavailable'
            wall.mode = 'ok'
            await entry.runtime_data.async_refresh()
            assert wall.writes == []
            assert await hass.config_entries.async_unload(entry.entry_id)
            print('actual HA diagnostics: default-disabled details/image, zero idle optional reads, shared polling, state deduplication, failed/stopped, host/receiver labels, image rate limit, unload/no writes passed', flush=True)
        finally:
            await hass.async_stop()
    await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(run())

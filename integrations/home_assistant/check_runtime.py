"""Run actual Home Assistant actions against a loopback fake wall, never hardware.

uv run --python 3.14 --with homeassistant==2026.9.2 --with pyyaml python \
    integrations/home_assistant/check_runtime.py
"""
import asyncio
from pathlib import Path
import tempfile
import time

from aiohttp import web
from homeassistant import bootstrap, loader
from homeassistant.core import HomeAssistant
import yaml

async def main():
    state = dict(schema='ledgrid.composer-settings-observation', schema_version=1,
                 controller_session_id='fake-session', controller_state_revision=7,
                 observed_at=time.time(), is_running=True, brightness=255,
                 animation_speed_scale=0.3, last_command_id=0,
                 active_identity={'scene_digest': 'retained'})
    writes = []
    mode = {'http': 200, 'apply': True}
    async def observe(request):
        state['observed_at'] = time.time()
        return web.json_response(state, status=mode['http'])
    async def command(request):
        payload = await request.json()
        writes.append((request.match_info['route'], payload))
        command_id = len(writes)
        if mode['apply']:
            assert payload['expected_controller_session_id'] == state['controller_session_id']
            assert payload['expected_controller_state_revision'] == state['controller_state_revision']
            assert payload['expires_at'] > time.time()
            for key, value in payload.items():
                if key == 'brightness': state['brightness'] = value
                if key == 'power': state['is_running'] = value
                if key == 'multiplier': state['animation_speed_scale'] = value * 0.3
            state['controller_state_revision'] += 1
            state['last_command_id'] = command_id
        return web.json_response({'success': True, 'command_id': command_id})
    app = web.Application()
    app.router.add_get('/api/v1/composer/settings/observed', observe)
    app.router.add_post('/api/config/{route}', command)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    package = yaml.safe_load(Path(__file__).with_name('led_grid_wall.yaml').read_text().replace('ledgridwall.local:5000', f'127.0.0.1:{port}'))
    with tempfile.TemporaryDirectory(prefix='ledgrid-ha-runtime-') as config_dir:
        hass = HomeAssistant(config_dir)
        loader.async_setup(hass)
        try:
            result = await bootstrap.async_from_config_dict({'homeassistant': {'name': 'Fake wall', 'latitude': 40.7, 'longitude': -74., 'elevation': 0, 'unit_system': 'metric', 'time_zone': 'America/New_York'}, **package}, hass)
            assert result is hass
            await hass.async_start()
            await hass.async_block_till_done()
            await asyncio.sleep(1)
            assert writes == [], writes
            print('startup: zero writes', flush=True)
            print([(s.entity_id, s.state) for s in hass.states.async_all() if 'led_grid' in s.entity_id], flush=True)
            assert hass.states.get('light.led_grid_wall').state == 'on'
            await hass.services.async_call('script', 'led_grid_wall_apply', {'brightness': 26}, blocking=True)
            await hass.async_block_till_done()
            assert state['brightness'] == 26
            assert hass.states.get('light.led_grid_wall').attributes['brightness'] == 26
            await hass.services.async_call('light', 'turn_off', {'entity_id': 'light.led_grid_wall'}, blocking=True)
            await hass.async_block_till_done()
            assert not state['is_running']
            await hass.services.async_call('light', 'turn_on', {'entity_id': 'light.led_grid_wall', 'brightness_pct': 10}, blocking=True)
            await hass.async_block_till_done()
            assert state['is_running'] and state['brightness'] == 26
            await hass.services.async_call('number', 'set_value', {'entity_id': 'number.led_grid_wall_pace', 'value': 1.25}, blocking=True)
            await hass.async_block_till_done()
            assert state['animation_speed_scale'] == .375
            await hass.services.async_call('script', 'led_grid_wall_apply', {'brightness': 0}, blocking=True)
            assert state['brightness'] == 0 and state['is_running']
            assert state['active_identity'] == {'scene_digest': 'retained'}
            print('brightness 0/26, power, pace, retained Scene: passed', flush=True)
            state['brightness'] = 17
            state['controller_state_revision'] += 1
            count = len(writes)
            hass.bus.async_fire('led_grid_wall_refresh')
            await hass.async_block_till_done()
            assert hass.states.get('light.led_grid_wall').attributes['brightness'] == 17
            assert len(writes) == count
            mode['http'] = 503
            hass.bus.async_fire('led_grid_wall_refresh')
            await hass.async_block_till_done()
            assert hass.states.get('light.led_grid_wall').state == 'unavailable'
            mode['http'] = 200
            hass.bus.async_fire('led_grid_wall_refresh')
            await hass.async_block_till_done()
            assert len(writes) == count
            print('manual change, HTTP failure, reconnect adoption: passed', flush=True)
            mode['apply'] = False
            result = await hass.services.async_call('script', 'led_grid_wall_apply', {'brightness': 26}, blocking=True, return_response=True)
            assert result and result['success'] is False, result
            print('unacknowledged command returned failure:', result, flush=True)
            assert len(writes) == count + 1
            assert state['brightness'] == 17
            print('runtime acceptance passed', flush=True)
        finally:
            await hass.async_stop()
    await runner.cleanup()

if __name__ == '__main__':
    asyncio.run(main())

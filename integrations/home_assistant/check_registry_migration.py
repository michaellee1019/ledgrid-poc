"""Rehearse native wall migration/recovery with actual HA registry handlers."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import uuid

from homeassistant.components.config import entity_registry as registry_api
from homeassistant.helpers import area_registry as ar, entity_registry as er, label_registry as lr
from homeassistant.setup import async_setup_component
import yaml

from check_native_runtime import LoopbackWall, native_hass, serve, setup_flow
from migrate_registry import ENTITIES, execute, make_plan, projection, registry_snapshot


class RegistryTransport:
    def __init__(self, hass):
        self.hass = hass
        self.writes = 0
        self.fail_at = None
        self.user = SimpleNamespace(is_admin=True)
        self.response = None
        self.handlers = {handler._ws_command: handler for handler in (
            registry_api.websocket_list_entities, registry_api.websocket_get_entities,
            registry_api.websocket_get_entity, registry_api.websocket_update_entity,
        )}

    def send_message(self, message):
        self.response = json.loads(message) if isinstance(message, (str, bytes)) else message

    def send_result(self, request_id, result):
        self.response = {'id': request_id, 'success': True, 'result': result}

    def send_error(self, request_id, code, message):
        self.response = {'id': request_id, 'success': False, 'error': {'code': code, 'message': message}}

    async def __call__(self, message):
        if message['type'].endswith('/update'):
            self.writes += 1
            if self.writes == self.fail_at:
                raise RuntimeError('Injected connection loss')
        handler = self.handlers[message['type']]
        message = {'id': 1, **message}
        if handler._ws_schema:
            message = handler._ws_schema(message)
        handler(self.hass, self, message)
        if not self.response['success']:
            raise RuntimeError(self.response.get('error'))
        return json.loads(json.dumps(self.response['result']))


async def main():
    wall = LoopbackWall()
    wall.identity = str(uuid.uuid4())
    runner, url = await serve(wall.app())
    with tempfile.TemporaryDirectory(prefix='ledgrid-ha-migration-') as config_dir:
        hass = await native_hass(config_dir)
        try:
            entities = er.async_get(hass)
            area = ar.async_get(hass).async_create('Synthetic wall room')
            label = lr.async_get(hass).async_create('Synthetic wall label')
            legacy = []
            for domain, legacy_key, native_key in ENTITIES:
                entry = entities.async_get_or_create(domain, 'template', legacy_key, suggested_object_id=legacy_key)
                entry = entities.async_update_entity(entry.entity_id, name=f'Synthetic {native_key}', icon='mdi:lightbulb',
                    area_id=area.id, aliases=['Synthetic wall alias', er.COMPUTED_NAME], labels={label.label_id},
                    categories={'test': 'synthetic-category'}, hidden_by=er.RegistryEntryHider.USER if native_key == 'observation' else None)
                entry = entities.async_update_entity_options(entry.entity_id, 'conversation', {'should_expose': True})
                legacy.append(projection(entry.extended_dict))
            setup = await setup_flow(hass, url)
            assert setup['type'] == 'create_entry', setup
            transport = RegistryTransport(hass)
            plan = make_plan(await registry_snapshot(transport), wall.identity)
            original = [projection(row) for row in await registry_snapshot(transport) if row['id'] in {e['id'] for pair in plan['pairs'] for e in (pair['legacy'], pair['native'])}]
            assert transport.writes == 0 and wall.writes == []
            # Preflight collisions are detected before touching even the first ID.
            reserved = entities.async_get_or_create('light', 'test', 'collision', suggested_object_id='led_grid_wall_legacy_light')
            try:
                make_plan(await registry_snapshot(transport), wall.identity)
                raise AssertionError('Expected collision rejection')
            except ValueError as err:
                assert 'occupied' in str(err)
            entities.async_remove(reserved.entity_id)
            # A halfway failure has no automatic rollback; explicit resume uses
            # the saved plan and verifies every already-completed operation.
            transport.fail_at = 4
            try:
                await execute(plan, transport)
                raise AssertionError('Expected injected failure')
            except RuntimeError as err:
                assert 'Injected' in str(err)
            transport.fail_at = None
            await execute(plan, transport)
            await hass.async_block_till_done()
            writes = transport.writes
            await execute(plan, transport)
            assert transport.writes == writes, 'Repeating a complete plan wrote again'
            for old in legacy:
                current = entities.async_get(old['entity_id'])
                assert current.platform == 'led_grid_wall'
                for key in ('name', 'area_id', 'icon', 'aliases', 'labels', 'categories', 'options', 'hidden_by'):
                    assert projection(current.extended_dict)[key] == old[key], (key, current)
            assert wall.writes == [], 'Registry migration replayed wall intent'
            # The compatibility script keeps the public service and response shape.
            package = yaml.safe_load(Path(__file__).with_name('led_grid_wall_native.yaml').read_text())
            Path(config_dir, 'configuration.yaml').write_text(yaml.safe_dump(package))
            if hass.services.has_service('script', 'reload'):
                await hass.services.async_call('script', 'reload', {}, blocking=True)
            else:
                assert await async_setup_component(hass, 'script', package)
            await hass.async_block_till_done()
            result = await hass.services.async_call('script', 'led_grid_wall_apply', {'brightness': 0, 'power': True}, blocking=True, return_response=True)
            assert result['success'] and wall.state['settings']['brightness'] == 0 and wall.state['settings']['power']
            count = len(wall.writes)
            for values in ({}, {'brightness': True}, {'multiplier': 0}):
                result = await hass.services.async_call('script', 'led_grid_wall_apply', values, blocking=True, return_response=True)
                assert result['success'] is False
            assert len(wall.writes) == count
            wall.mode = 'reject'
            result = await hass.services.async_call('script', 'led_grid_wall_apply', {'brightness': 26}, blocking=True, return_response=True)
            assert result['success'] is False and result['error']
            wall.mode = 'ok'
            # User registry changes are not overwritten by recovery.
            changed = entities.async_get('light.led_grid_wall')
            entities.async_update_entity(changed.entity_id, name='New manual name')
            try:
                await execute(plan, transport, reverse=True)
                raise AssertionError('Expected manual-change conflict')
            except RuntimeError as err:
                assert 'changed since planning' in str(err)
            entities.async_update_entity('light.led_grid_wall', name=legacy[0]['name'])
            await execute(plan, transport, reverse=True)
            await hass.async_block_till_done()
            restored = [projection(row) for row in await registry_snapshot(transport) if row['id'] in {e['id'] for e in original}]
            assert sorted(restored, key=lambda e:e['id']) == sorted(original, key=lambda e:e['id'])
            print('Actual HA registry migration: IDs/metadata/collisions/partial failure/resume/restore/manual conflicts passed')
            print('Native compatibility script: zero/omitted fields/invalid controls/command error responses passed')
        finally:
            await hass.async_stop()
    await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())

"""Structured advanced actions share the guarded Scene command path."""
import math
import voluptuous as vol

from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN

SERVICES = ('set_scene', 'update_component', 'configure_widget', 'interact')


def number(value):
    if type(value) not in {int, float} or not math.isfinite(value):
        raise vol.Invalid('Expected a finite number')
    return value


async def async_setup_advanced(hass):
    targets = {vol.Optional('entity_id'): cv.entity_ids,
               vol.Optional('device_id'): vol.All(cv.ensure_list, [cv.string])}
    fields = {
        'set_scene': {vol.Optional('look'): dict, vol.Optional('background_brightness'): number, vol.Optional('plants'): dict},
        'update_component': {vol.Required('target'): vol.In(('animation', 'background', 'widget')),
            vol.Required('component_id'): cv.string, vol.Optional('widget_id'): cv.string, vol.Required('parameters'): dict},
        'configure_widget': {vol.Required('operation'): vol.In(('add', 'update', 'remove')), vol.Optional('id'): cv.string,
            vol.Optional('widget'): dict, vol.Optional('component_id'): cv.string, vol.Optional('parameters'): dict,
            vol.Optional('placement'): dict, vol.Optional('visible'): cv.boolean},
        'interact': {vol.Required('component_id'): cv.string, vol.Required('kind'): cv.string,
            vol.Required('x'): number, vol.Required('y'): number, vol.Optional('strength', default=1): number},
    }
    for service in SERVICES:
        if hass.services.has_service(DOMAIN, service):
            continue
        async def action(call):
            entries = [item for item in hass.config_entries.async_entries(DOMAIN) if getattr(item, 'runtime_data', None) is not None]
            if len(entries) != 1:
                raise HomeAssistantError('Exactly one loaded wall is required')
            entry = entries[0]
            registry, devices = er.async_get(hass), dr.async_get(hass)
            for entity_id in call.data.get('entity_id', []):
                entity = registry.async_get(entity_id)
                if entity is None or entity.config_entry_id != entry.entry_id:
                    raise HomeAssistantError('Target must belong to this wall')
            for device_id in call.data.get('device_id', []):
                device = devices.async_get(device_id)
                if device is None or (DOMAIN, entry.unique_id) not in device.identifiers:
                    raise HomeAssistantError('Target must be this wall device')
            body = {key: value for key, value in call.data.items() if key not in {'entity_id', 'device_id'}}
            try:
                if call.service == 'interact':
                    return await entry.runtime_data.async_interact(body)
                patch = body if call.service == 'set_scene' else {'component' if call.service == 'update_component' else 'widget': body}
                return await entry.runtime_data.async_patch_scene(patch)
            except HomeAssistantError as err:
                return {'success': False, 'error': str(err)}
        hass.services.async_register(DOMAIN, service, action, schema=vol.Schema({**targets, **fields[service]}),
                                     supports_response=SupportsResponse.OPTIONAL)

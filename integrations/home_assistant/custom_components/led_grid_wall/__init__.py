"""Native Home Assistant integration for one locally hosted LED Grid Wall."""
import math

import voluptuous as vol

from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr, entity_registry as er
import homeassistant.helpers.config_validation as cv

from .api import WallApi, WallError
from .const import CONF_URL, DOMAIN, PLATFORMS
from .coordinator import WallCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def strict_int(value):
    if type(value) is not int:
        raise vol.Invalid("Expected an integer")
    return value


def strict_bool(value):
    if type(value) is not bool:
        raise vol.Invalid("Expected a boolean")
    return value


def finite_number(value):
    if type(value) not in {int, float} or not math.isfinite(value):
        raise vol.Invalid("Expected a finite number")
    return float(value)


APPLY_SCHEMA = vol.All(
    vol.Schema({
        vol.Optional("entity_id"): cv.entity_ids,
        vol.Optional("device_id"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("brightness"): vol.All(strict_int, vol.Range(min=0, max=255)),
        vol.Optional("power"): strict_bool,
        vol.Optional("multiplier"): vol.All(finite_number, vol.Range(min=0.1, max=25)),
    }),
    cv.has_at_least_one_key("brightness", "power", "multiplier"),
)


async def async_setup_entry(hass, entry):
    api = WallApi(async_get_clientsession(hass), entry.data[CONF_URL])
    # Verify that a changed address did not silently substitute another wall.
    try:
        capabilities = await api.capabilities()
        if capabilities["installation_id"] != entry.unique_id:
            raise WallError("The address belongs to a different wall; reconfigure the URL")
    except WallError as err:
        from homeassistant.exceptions import ConfigEntryNotReady
        raise ConfigEntryNotReady(str(err)) from err
    coordinator = WallCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.services.has_service(DOMAIN, "apply"):
        async def async_apply(call):
            entries = hass.config_entries.async_entries(DOMAIN)
            loaded = [item for item in entries if hasattr(item, "runtime_data") and item.runtime_data is not None]
            if len(loaded) != 1:
                raise HomeAssistantError("Exactly one loaded LED Grid Wall is required")
            selected = loaded[0]
            registry = er.async_get(hass)
            devices = dr.async_get(hass)
            for entity_id in call.data.get("entity_id", []):
                entity = registry.async_get(entity_id)
                if entity is None or entity.config_entry_id != selected.entry_id or entity.domain != "light":
                    raise HomeAssistantError("Target must be this integration's wall light")
            for device_id in call.data.get("device_id", []):
                device = devices.async_get(device_id)
                if device is None or (DOMAIN, selected.unique_id) not in device.identifiers:
                    raise HomeAssistantError("Target must be this integration's wall device")
            try:
                fields = {key: value for key, value in call.data.items() if key not in {"entity_id", "device_id"}}
                return await selected.runtime_data.async_apply(fields)
            except HomeAssistantError as err:
                return {"success": False, "error": str(err)}

        hass.services.async_register(DOMAIN, "apply", async_apply, schema=APPLY_SCHEMA, supports_response=SupportsResponse.OPTIONAL)
    return True


async def async_unload_entry(hass, entry):
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        entry.runtime_data = None
        if not any(item.runtime_data is not None for item in hass.config_entries.async_entries(DOMAIN) if hasattr(item, "runtime_data")):
            hass.services.async_remove(DOMAIN, "apply")
    return unloaded

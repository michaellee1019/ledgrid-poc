"""URL setup and reconfiguration with stable identity duplicate protection."""
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import WallApi, WallError, normalize_url
from .const import CONF_URL, DOMAIN


class WallConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        return await self._async_url_step("user", user_input)

    async def async_step_reconfigure(self, user_input=None):
        return await self._async_url_step("reconfigure", user_input)

    async def _async_url_step(self, step, user_input):
        errors = {}
        entry = self._get_reconfigure_entry() if step == "reconfigure" else None
        if user_input is not None:
            try:
                url = normalize_url(user_input[CONF_URL])
                api = WallApi(async_get_clientsession(self.hass), url)
                identity = (await api.capabilities())["installation_id"]
                await api.status(identity)
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            except WallError:
                errors["base"] = "cannot_connect"
            else:
                if entry:
                    if identity != entry.unique_id:
                        errors["base"] = "wrong_wall"
                    else:
                        return self.async_update_reload_and_abort(entry, data_updates={CONF_URL: url})
                else:
                    await self.async_set_unique_id(identity)
                    self._abort_if_unique_id_configured()
                    if self._async_current_entries():
                        return self.async_abort(reason="single_instance_allowed")
                    return self.async_create_entry(title="LED Grid Wall", data={CONF_URL: url})
        default = entry.data[CONF_URL] if entry else "http://ledgridwall.local:5000"
        return self.async_show_form(step_id=step, data_schema=vol.Schema({vol.Required(CONF_URL, default=default): str}), errors=errors)

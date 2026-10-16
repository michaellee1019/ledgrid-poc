"""One shared controls/status poll for all entities."""
import asyncio
import logging

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import WallError
from .const import DOMAIN, POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)


class WallCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, api):
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=POLL_INTERVAL)
        self.api = api
        self.installation_id = entry.unique_id
        self.command_lock = asyncio.Lock()

    async def _async_update_data(self):
        try:
            return await self.api.status(self.installation_id)
        except WallError as err:
            raise UpdateFailed(str(err)) from err

    @property
    def observation(self):
        state = self.data
        return {
            "controller_session_id": state["controller_session_id"],
            "controller_state_revision": state["controller_state_revision"],
            "observed_at": state["observed_at"],
            "is_running": state["settings"]["power"],
            "brightness": state["settings"]["brightness"],
            "animation_speed_scale": state["settings"]["animation_speed_scale"],
            "last_command_id": state.get("last_command_id"),
            "last_applied_command_id": state.get("last_applied_command_id"),
            "active_identity": state.get("active_identity", state.get("requested_scene")),
        }

    async def async_apply(self, fields):
        if self.command_lock.locked():
            raise HomeAssistantError("A wall command is already in progress; no command queued")
        async with self.command_lock:
            try:
                before = await self.api.status(self.installation_id)
                receipt = await self.api.apply(before, fields)
            except WallError as err:
                raise HomeAssistantError(str(err)) from err
            finally:
                await self.async_refresh()
            if not self.last_update_success:
                raise HomeAssistantError("Command completed but current wall status is unavailable")
            return {
                "success": True,
                "request_id": receipt["request_id"],
                "command_id": receipt["command_id"],
                "controller_session_id": receipt.get("controller_session_id"),
                "controller_state_revision": receipt.get("controller_state_revision"),
            }

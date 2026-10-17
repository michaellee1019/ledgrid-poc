"""One shared controls/status poll for all entities."""
import asyncio
import logging

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import WallError
from .const import CATALOG_INTERVAL, DOMAIN, POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)


class WallCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, api):
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=POLL_INTERVAL)
        self.api = api
        self.installation_id = entry.unique_id
        self.command_lock = asyncio.Lock()
        self.supports_content = False
        self.catalog = None
        self.catalog_error = None

    async def async_refresh_catalog(self, _now=None):
        try:
            self.catalog = await self.api.catalog(self.installation_id)
            self.catalog_error = None
        except WallError as err:
            self.catalog_error = str(err)
            self.async_update_listeners()
            raise HomeAssistantError(str(err)) from err
        self.async_update_listeners()

    def async_start_catalog_poll(self, entry):
        async def refresh(now):
            try:
                await self.async_refresh_catalog(now)
            except HomeAssistantError:
                pass
        entry.async_on_unload(async_track_time_interval(self.hass, refresh, CATALOG_INTERVAL))

    async def async_play_content(self, content_id):
        return await self._async_command({'content_id': content_id}, action='play_content')

    async def async_stop_playlist(self):
        return await self._async_command({}, action='stop_playlist')


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
        return await self._async_command(fields)

    async def _async_command(self, fields, *, action='set_device_state'):
        if self.command_lock.locked():
            raise HomeAssistantError("A wall command is already in progress; no command queued")
        async with self.command_lock:
            try:
                before = await self.api.status(self.installation_id)
                if action == 'set_device_state':
                    receipt = await self.api.apply(before, fields)
                else:
                    if action == 'stop_playlist':
                        playlist = before.get('playlist') or {}
                        if playlist.get('phase') != 'running' or not playlist.get('run_id'):
                            raise WallError('No observed playlist is running')
                        fields = {'run_id': playlist['run_id']}
                    receipt = await self.api.command(before, fields, action=action)
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

"""Explicit catalog refresh and controller-owned playlist cancellation."""
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import WallEntity


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.runtime_data.supports_content:
        async_add_entities([WallCatalogRefresh(entry.runtime_data), WallStopPlaylist(entry.runtime_data)])


class WallCatalogRefresh(WallEntity, ButtonEntity):
    _attr_name = 'Refresh content catalog'
    _attr_icon = 'mdi:refresh'
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator):
        super().__init__(coordinator, 'refresh_catalog')
        self._attr_suggested_object_id = 'led_grid_wall_refresh_catalog'

    async def async_press(self):
        await self.coordinator.async_refresh_catalog()


class WallStopPlaylist(WallEntity, ButtonEntity):
    _attr_name = 'Stop playlist'
    _attr_icon = 'mdi:playlist-remove'

    def __init__(self, coordinator):
        super().__init__(coordinator, 'stop_playlist')
        self._attr_suggested_object_id = 'led_grid_wall_stop_playlist'

    @property
    def available(self):
        return super().available and (self.coordinator.data.get('playlist') or {}).get('phase') == 'running'

    async def async_press(self):
        await self.coordinator.async_stop_playlist()

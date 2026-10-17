"""Optional existing controller buffer, never a camera or physical-display proof."""
from datetime import datetime, timezone

from homeassistant.components.image import ImageEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .api import WallError
from .entity import WallEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([WallControllerImage(hass, entry.runtime_data)])


class WallControllerImage(WallEntity, ImageEntity):
    _attr_name = 'Controller output'
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_content_type = 'image/png'

    def __init__(self, hass, coordinator):
        ImageEntity.__init__(self, hass)
        WallEntity.__init__(self, coordinator, 'controller_output')
        self._last_available = None

    @callback
    def _handle_coordinator_update(self):
        if self.available != self._last_available:
            self._last_available = self.available
            self.async_write_ha_state()

    async def async_image(self):
        try:
            content, stamp = await self.coordinator.diagnostics.async_frame()
        except WallError as err:
            raise HomeAssistantError(str(err)) from err
        updated = datetime.fromtimestamp(stamp, timezone.utc)
        if self._attr_image_last_updated != updated:
            self._attr_image_last_updated = updated
            self.async_write_ha_state()
        return content

    @property
    def extra_state_attributes(self):
        return {'source': 'existing controller output buffer', 'physical_display_verified': False}

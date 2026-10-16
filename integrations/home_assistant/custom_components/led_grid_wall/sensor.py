"""Compatibility observation; controller acknowledgement is not physical proof."""
from homeassistant.components.sensor import SensorEntity

from .entity import WallEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([WallObservation(entry.runtime_data)])


class WallObservation(WallEntity, SensorEntity):
    _attr_name = "Observation"
    _attr_icon = "mdi:eye-check-outline"

    def __init__(self, coordinator):
        super().__init__(coordinator, "observation")
        self._attr_suggested_object_id = "led_grid_wall_observation"

    @property
    def native_value(self):
        state = self.coordinator.observation
        return f"{state['controller_session_id']}:{state['controller_state_revision']}"

    @property
    def extra_state_attributes(self):
        return self.coordinator.observation

"""Observed operator pace, independent from scene-local pace and target FPS."""
from homeassistant.components.number import NumberEntity, NumberMode

from .const import PACE_BASELINE
from .entity import WallEntity


async def async_setup_entry(hass, entry, async_add_entities):
    entities = [WallPace(entry.runtime_data)]
    if entry.runtime_data.supports_advanced:
        from .scene_entities import scene_numbers
        entities.extend(scene_numbers(entry.runtime_data))
    async_add_entities(entities)


class WallPace(WallEntity, NumberEntity):
    _attr_name = "Pace"
    _attr_icon = "mdi:speedometer"
    _attr_native_min_value = 0.1
    _attr_native_max_value = 25.0
    _attr_native_step = 0.05
    _attr_native_unit_of_measurement = "x"
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator):
        super().__init__(coordinator, "pace")
        self._attr_suggested_object_id = "led_grid_wall_pace"

    @property
    def native_value(self):
        return self.coordinator.observation["animation_speed_scale"] / PACE_BASELINE

    async def async_set_native_value(self, value):
        await self.coordinator.async_apply({"multiplier": value})

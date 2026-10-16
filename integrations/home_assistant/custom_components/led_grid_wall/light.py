"""Observed retained-scene playback and master output brightness."""
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity

from .entity import WallEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([WallLight(entry.runtime_data)])


class WallLight(WallEntity, LightEntity):
    _attr_name = None
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator):
        super().__init__(coordinator, "light")
        self._attr_suggested_object_id = "led_grid_wall"

    @property
    def is_on(self):
        return self.coordinator.observation["is_running"]

    @property
    def brightness(self):
        return self.coordinator.observation["brightness"]

    async def async_turn_on(self, **kwargs):
        fields = {"power": True}
        if ATTR_BRIGHTNESS in kwargs:
            fields["brightness"] = kwargs[ATTR_BRIGHTNESS]
        await self.coordinator.async_apply(fields)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_apply({"power": False})

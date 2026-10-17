"""Dynamic content selectors, always derived from observed controller content."""
from homeassistant.components.select import SelectEntity

from .entity import WallEntity


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.runtime_data.supports_content:
        entities = [WallContentSelect(entry.runtime_data, kind, name) for kind, name in
                    [('animation', 'Animation'), ('preset', 'Preset'), ('look', 'Look'), ('playlist', 'Playlist')]]
        if entry.runtime_data.supports_advanced:
            from .scene_entities import WallPalette
            entities.append(WallPalette(entry.runtime_data))
        async_add_entities(entities)


class WallContentSelect(WallEntity, SelectEntity):
    _attr_icon = 'mdi:play-box-outline'

    def __init__(self, coordinator, kind, name):
        super().__init__(coordinator, kind)
        self.kind = kind
        self._attr_name = name
        self._attr_suggested_object_id = f'led_grid_wall_{kind}'

    @property
    def choices(self):
        return [item for item in (self.coordinator.catalog or {}).get('content', [])
                if item.get('kind') == self.kind and item.get('supported')]

    @property
    def options(self):
        return [item['label'] for item in self.choices]

    @property
    def current_option(self):
        identity = (self.coordinator.data.get('active_content') or {}).get(self.kind)
        return next((item['label'] for item in self.choices if item['id'] == identity), None)

    @property
    def extra_state_attributes(self):
        return {'content_id': (self.coordinator.data.get('active_content') or {}).get(self.kind),
                'catalog_error': self.coordinator.catalog_error,
                'playlist': self.coordinator.data.get('playlist') if self.kind == 'playlist' else None}

    async def async_select_option(self, option):
        identity = next((item['id'] for item in self.choices if item['label'] == option), None)
        if identity is None:
            from homeassistant.exceptions import HomeAssistantError
            raise HomeAssistantError('Content is no longer in the current catalog; refresh it.')
        await self.coordinator.async_play_content(identity)

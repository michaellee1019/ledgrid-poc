"""Scene-owned Illuminate, Shadow and Hue shift toggles."""
from .scene_entities import COMMON_EFFECTS, WallSceneEffect


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.runtime_data.supports_advanced:
        async_add_entities([WallSceneEffect(entry.runtime_data, effect) for effect in COMMON_EFFECTS])

"""Entities share a single observed wall and device identity."""
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class WallEntity(CoordinatorEntity):
    """Common device metadata, with availability supplied by the coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, key):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.installation_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.installation_id)},
            name="LED Grid Wall",
            manufacturer="LED Grid",
            model="33×138 wall (five receivers)",
            configuration_url=coordinator.api.url + "/composer",
        )

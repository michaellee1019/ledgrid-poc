"""Compatibility observation; controller acknowledgement is not physical proof."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from .diagnostics import async_setup_diagnostics

from .entity import WallEntity


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


# Small status projections reuse the existing two-second poll. Optional counters
# subscribe to one independent thirty-second coordinator only when enabled.
def content_label(value):
    if not isinstance(value, dict):
        return 'none'
    for key in ('name', 'label', 'content_id', 'id', 'look', 'preset', 'animation', 'playlist'):
        if isinstance(value.get(key), str) and value[key]:
            return value[key][:255]
    for key in ('animation', 'background'):
        component = value.get(key)
        if isinstance(component, dict) and component.get('component_id'):
            return component['component_id'][:255]
    return str(value.get('digest') or 'none')[:255]


def playback_state(data):
    playback = data.get('controller_playback') or {}
    return 'failed' if playback.get('error') else playback.get('state', 'unknown')


class WallStatusSensor(WallEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key, name, getter):
        super().__init__(coordinator, key)
        self._attr_name = name
        self.getter = getter
        self._last_projection = None

    @property
    def native_value(self):
        return self.getter(self.coordinator.data or {})

    @callback
    def _handle_coordinator_update(self):
        projection = (self.available, self.native_value, self.extra_state_attributes)
        if projection != self._last_projection:
            self._last_projection = projection
            self.async_write_ha_state()


class WallActiveContent(WallStatusSensor):
    def __init__(self, coordinator):
        super().__init__(coordinator, 'active_content', 'Active content', lambda data: content_label(data.get('active_content')))

    @property
    def extra_state_attributes(self):
        content = (self.coordinator.data or {}).get('active_content') or {}
        return {key: content.get(key) for key in ('animation', 'preset', 'look', 'playlist')}


class ControllerConnectivity(WallStatusSensor):
    def __init__(self, coordinator):
        super().__init__(coordinator, 'controller_connectivity', 'Controller connectivity', lambda data: 'online' if coordinator.last_update_success else 'offline')

    @property
    def available(self):
        return True


class WallDiagnosticSensor(WallEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, key, name, getter, unit=None):
        super().__init__(coordinator, key)
        self._attr_name = name
        self.getter = getter
        self._attr_native_unit_of_measurement = unit
        self._last_projection = None

    @property
    def available(self):
        return super().available and self.coordinator.wall.last_update_success

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.wall.async_add_listener(self._handle_coordinator_update))
        await self.coordinator.async_ensure_data()

    @callback
    def _handle_coordinator_update(self):
        projection = (self.available, self.native_value)
        if projection != self._last_projection:
            self._last_projection = projection
            self.async_write_ha_state()

    @property
    def native_value(self):
        return self.getter(self.coordinator.data or {})


def diagnostic_sensors(coordinator):
    sensors = []
    for key, name, unit in (
        ('host_target_fps', 'Host target cadence', 'fps'),
        ('host_actual_fps', 'Host actual cadence', 'fps'),
        ('host_frames_generated', 'Host frames generated', 'frames'),
        ('host_frames_presented', 'Host frames presented', 'frames'),
        ('unchanged_frames_skipped', 'Host unchanged frames skipped', 'frames'),
        ('avg_generate_ms', 'Host average generation time', 'ms'),
        ('avg_send_ms', 'Host average send time', 'ms'),
        ('p95_frame_ms', 'Host p95 frame time', 'ms'),
        ('deadline_misses', 'Host deadline misses', 'frames'),
    ):
        sensors.append(WallDiagnosticSensor(coordinator, key, name, lambda data, key=key: data.get('controller', {}).get(key), unit))
    for receiver_id in range(5):
        for key, label, unit in (
            ('output_fps', 'Measured output cadence', 'fps'),
            ('protected_status', 'Protected status', None),
            ('output_rate_reason', 'Output cadence evidence', None),
            ('crc_errors', 'CRC errors', 'errors'),
            ('fec_corrected_packets', 'FEC corrected packets', 'packets'),
            ('fec_uncorrectable_packets', 'FEC uncorrectable packets', 'packets'),
            ('fec_semantic_crc_errors', 'FEC semantic CRC errors', 'errors'),
            ('fec_framing_errors', 'FEC framing errors', 'errors'),
            ('status_integrity_errors', 'Status integrity errors', 'errors'),
        ):
            def value(data, key=key, receiver_id=receiver_id):
                receiver = data.get('receivers', {}).get(receiver_id, {})
                return receiver.get(key) if key in receiver else receiver.get('counters', {}).get(key)
            sensors.append(WallDiagnosticSensor(coordinator, f'receiver_{receiver_id}_{key}', f'Receiver {receiver_id} {label.lower()}', value, unit))
    return sensors


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    if not hasattr(coordinator, 'diagnostics'):
        await async_setup_diagnostics(hass, entry)
    sensors = [
        WallObservation(coordinator), ControllerConnectivity(coordinator),
        WallStatusSensor(coordinator, 'requested_content', 'Requested content', lambda data: content_label(data.get('requested_scene'))),
        WallActiveContent(coordinator),
        WallStatusSensor(coordinator, 'controller_playback', 'Controller playback', playback_state),
        WallStatusSensor(coordinator, 'controller_error', 'Controller error', lambda data: str((data.get('controller_playback') or {}).get('error') or 'none')[:255]),
        WallStatusSensor(coordinator, 'playlist_status', 'Playlist status', lambda data: (data.get('playlist') or {}).get('phase', 'idle')),
        WallStatusSensor(coordinator, 'playlist_error', 'Playlist error', lambda data: str((data.get('playlist') or {}).get('error') or 'none')[:255]),
    ]
    for index in range(5):
        def connectivity(data, index=index):
            receiver = next((item for item in data.get('receiver_connectivity', []) if item.get('device_index') == index), {})
            return 'connected' if receiver.get('connected') is True else 'disconnected' if receiver.get('connected') is False else 'unknown'
        sensors.append(WallStatusSensor(coordinator, f'receiver_{index}_connectivity', f'Receiver {index} connectivity', connectivity))
    async_add_entities(sensors + diagnostic_sensors(coordinator.diagnostics))

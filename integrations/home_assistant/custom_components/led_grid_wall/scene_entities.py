"""Observed Scene controls, separate from installation output settings."""
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import WallEntity

PALETTES = ('neutral', 'mist', 'ember', 'spectrum')
COMMON_EFFECTS = ('illuminate', 'shadow', 'hue_shift')


class SceneEntity(WallEntity):
    @property
    def controls(self):
        return self.coordinator.data.get('scene_controls') or {}

    @property
    def available(self):
        return super().available and bool(self.controls)


class WallSceneNumber(SceneEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_step = .01
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, key, name, maximum, *, effect=None):
        super().__init__(coordinator, key)
        self.key, self.effect = key, effect
        self._attr_name = name
        self._attr_native_max_value = maximum
        self._attr_suggested_object_id = f'led_grid_wall_{key}'
        if key == 'target_fps':
            self._attr_native_min_value = 1
            self._attr_native_step = 1
            self._attr_native_unit_of_measurement = 'FPS'
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self):
        if self.key == 'target_fps':
            return WallEntity.available.fget(self)
        return super().available and (not self.effect or self.effect in self.controls.get('plants', {}).get('active', []))

    @property
    def native_value(self):
        if self.effect:
            return self.controls.get('plants', {}).get('strengths', {}).get(self.effect)
        if self.key == 'target_fps':
            return self.coordinator.data['settings'].get('target_fps')
        if self.key == 'background_brightness':
            return self.controls.get('background_brightness')
        return self.controls.get('look', {}).get({'scene_pace': 'pace', 'presentation_brightness': 'presentation_brightness'}[self.key])

    async def async_set_native_value(self, value):
        if self.key == 'target_fps':
            await self.coordinator.async_set_target_fps(value)
        elif self.effect:
            await self.coordinator.async_patch_scene({'plants': {self.effect: {'strength': value}}})
        elif self.key == 'background_brightness':
            await self.coordinator.async_patch_scene({'background_brightness': value})
        else:
            await self.coordinator.async_patch_scene({'look': {{'scene_pace': 'pace', 'presentation_brightness': 'presentation_brightness'}[self.key]: value}})


def scene_numbers(coordinator):
    return [WallSceneNumber(coordinator, *fields) for fields in [
        ('scene_pace', 'Scene pace', 2), ('presentation_brightness', 'Presentation brightness', 2),
        ('background_brightness', 'Background brightness', 1), ('target_fps', 'Target FPS', 200)]] + [
        WallSceneNumber(coordinator, effect + '_strength', effect.replace('_', ' ').title() + ' strength', 1, effect=effect)
        for effect in COMMON_EFFECTS]


class WallSceneEffect(SceneEntity, SwitchEntity):
    def __init__(self, coordinator, effect):
        super().__init__(coordinator, effect)
        self.effect = effect
        self._attr_name = effect.replace('_', ' ').title()
        self._attr_suggested_object_id = f'led_grid_wall_{effect}'

    @property
    def is_on(self):
        return self.effect in self.controls.get('plants', {}).get('active', [])

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_patch_scene({'plants': {self.effect: {'enabled': True}}})

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_patch_scene({'plants': {self.effect: {'enabled': False}}})


class WallPalette(SceneEntity, SelectEntity):
    _attr_name = 'Palette'
    _attr_options = list(PALETTES)

    def __init__(self, coordinator):
        super().__init__(coordinator, 'palette')
        self._attr_suggested_object_id = 'led_grid_wall_palette'

    @property
    def current_option(self):
        palette = self.controls.get('look', {}).get('palette_id')
        return palette if palette in PALETTES else None

    async def async_select_option(self, option):
        await self.coordinator.async_patch_scene({'look': {'palette_id': option}})

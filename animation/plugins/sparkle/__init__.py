#!/usr/bin/env python3
"""
Sparkle Animation Plugin

Keeps only the sparkle effect from the previous effects collection so it can
stand alone as a single plugin.
"""

import numpy as np
from typing import Dict, Any
from animation import AnimationBase
from animation.libraries.mask_effects import build_halo_weights


class SparkleAnimation(AnimationBase):
    """Random sparkle effect over a dim base color"""

    ANIMATION_NAME = "Sparkle"
    ANIMATION_DESCRIPTION = "Random sparkling lights effect"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.2"
    PLANT_MODIFIER_SUPPORT = frozenset((
        "illuminate", "attractor", "repulsor", "habitat", "emitter",
    ))

    def __init__(self, controller, config: Dict[str, Any] = None):
        super().__init__(controller, config)

        self.default_params.update({
            'base_red': 34,
            'base_green': 22,
            'base_blue': 8,
            'sparkle_red': 255,
            'sparkle_green': 200,
            'sparkle_blue': 80,
            'sparkle_probability': 0.02,
            'fade_speed': 0.9,
        })

        self.params = {**self.default_params, **self.config}

        total_pixels = self.get_pixel_count()
        self.sparkle_brightness = np.zeros(total_pixels, dtype=np.float32)
        self._color_scratch = np.empty((total_pixels, 3), dtype=np.float32)
        self._plant_field_masks = None
        self._plant_spawn_multiplier = None
        self._plant_foliage = None
        self._plant_globes = None
        self._plant_field_key = None
        self._plant_emitter_edge = None
        self._emitted_last_frame = 0
        self._emitted_total = 0
        self._last_emitter_elapsed = None

    def _ensure_plant_field(self, masks):
        """Cache plant-aware spawn weights for the current calibrated maps."""
        if self._plant_field_masks is masks and self._plant_field_key == ("legacy", id(masks)):
            return

        strip_count, leds_per_strip = self.get_strip_info()
        radius = max(2, int(self.params.get('plant_clearance', 1)) + 2)
        _, foliage_halo = build_halo_weights(
            np.flatnonzero(masks.foliage_flat),
            strip_count,
            leds_per_strip,
            radius,
            1.2,
        )
        _, globe_halo = build_halo_weights(
            np.flatnonzero(masks.globes_flat),
            strip_count,
            leds_per_strip,
            radius,
            1.05,
        )

        # Leaves repel new points of light while glass globes gather a bright
        # rim.  The clearance zone remains usable at low probability so a
        # large mask cannot make a small installation go completely static.
        multiplier = np.ones(self.get_pixel_count(), dtype=np.float32)
        multiplier *= 1.0 - np.float32(0.85) * foliage_halo
        multiplier *= 1.0 + np.float32(2.25) * globe_halo
        multiplier[masks.clearance_flat] *= np.float32(0.12)
        multiplier[masks.obstacle_flat] = 0.0
        np.clip(multiplier, 0.0, 3.0, out=multiplier)

        self._plant_spawn_multiplier = multiplier
        self._plant_foliage = masks.foliage_flat
        self._plant_globes = masks.globes_flat
        self._plant_field_masks = masks
        self._plant_field_key = ("legacy", id(masks))

    def _accent_plant_cores(self, frame):
        """Render distinct, subdued leaf and glass landmarks."""
        brightness = float(self.params.get('brightness', 1.0))
        foliage = np.rint(np.asarray((18, 112, 38)) * brightness).astype(np.uint8)
        globe = np.rint(np.asarray((176, 48, 150)) * brightness).astype(np.uint8)
        frame[self._plant_foliage] = foliage
        frame[self._plant_globes] = globe

    def _legacy_plant_aware(self):
        payload = self.params.get('plant_modifiers')
        active = payload.get('active', ()) if isinstance(payload, dict) else ()
        return bool(self.params.get('plant_aware', False)) and not active

    def _ensure_modifier_field(self, masks, field, field_strength, habitat_strength):
        key = (id(masks), field, field_strength, habitat_strength)
        if key == self._plant_field_key:
            return
        strip_count, leds_per_strip = self.get_strip_info()
        edge_indices = np.flatnonzero(masks.obstacle_edge.ravel())
        _, edge_halo = build_halo_weights(
            edge_indices, strip_count, leds_per_strip,
            max(2, int(self.params.get('plant_clearance', 1)) + 3), 1.25,
        )
        edge_weight = edge_halo
        edge_weight[masks.obstacle_edge.ravel()] = 1.0
        multiplier = np.ones(self.get_pixel_count(), dtype=np.float32)
        if field == 'attractor' and field_strength > 0.0:
            multiplier *= 1.0 + np.float32(2.5 * field_strength) * edge_weight
        elif field == 'repulsor' and field_strength > 0.0:
            influence = np.clip(
                1.0 - masks.distance.ravel()
                / np.float32(2.0 + 5.0 * field_strength), 0.0, 1.0
            )
            multiplier *= 1.0 - np.float32(0.92 * field_strength) * influence
        if habitat_strength > 0.0:
            foliage_indices = np.flatnonzero(masks.foliage_edge.ravel())
            _, foliage_halo = build_halo_weights(
                foliage_indices, strip_count, leds_per_strip,
                max(2, int(self.params.get('plant_clearance', 1)) + 2), 1.2,
            )
            foliage_halo[masks.foliage_edge.ravel()] = 1.0
            multiplier *= 1.0 + np.float32(2.0 * habitat_strength) * foliage_halo
        np.clip(multiplier, 0.0, 4.0, out=multiplier)
        self._plant_spawn_multiplier = multiplier
        self._plant_foliage = masks.foliage_flat
        self._plant_globes = masks.globes_flat
        self._plant_emitter_edge = masks.obstacle_edge.ravel()
        self._plant_field_masks = masks
        self._plant_field_key = key

    def _emit_from_plants(self, random_values, sparkle_prob, elapsed, strength):
        self._emitted_last_frame = 0
        if strength <= 0.0 or self._plant_emitter_edge is None:
            return
        interval = 1.0 / (2.0 + 18.0 * strength)
        if (self._last_emitter_elapsed is not None
                and elapsed >= self._last_emitter_elapsed
                and elapsed - self._last_emitter_elapsed < interval):
            return
        self._last_emitter_elapsed = elapsed
        candidates = np.flatnonzero(
            self._plant_emitter_edge
            & (random_values < sparkle_prob * (0.35 + 2.65 * strength))
        )
        cap = max(1, min(32, int(1 + strength * 15)))
        emitted = candidates[:cap]
        if emitted.size:
            self.sparkle_brightness[emitted] = np.float32(0.45 + 0.55 * strength)
        self._emitted_last_frame = int(emitted.size)
        self._emitted_total += self._emitted_last_frame

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.update({
            'base_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 30, 'description': 'Base color red'},
            'base_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 18, 'description': 'Base color green'},
            'base_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 4, 'description': 'Base color blue'},
            'sparkle_red': {'type': 'int', 'min': 0, 'max': 255, 'default': 255, 'description': 'Sparkle color red'},
            'sparkle_green': {'type': 'int', 'min': 0, 'max': 255, 'default': 200, 'description': 'Sparkle color green'},
            'sparkle_blue': {'type': 'int', 'min': 0, 'max': 255, 'default': 80, 'description': 'Sparkle color blue'},
            'sparkle_probability': {'type': 'float', 'min': 0.001, 'max': 0.1, 'default': 0.02, 'description': 'Sparkle probability'},
            'fade_speed': {'type': 'float', 'min': 0.5, 'max': 0.99, 'default': 0.9, 'description': 'Fade speed'},
        })
        return schema

    def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
        """Generate sparkle frame. Returns (N,3) uint8 ndarray."""
        total_pixels = self.get_pixel_count()

        base = np.array([
            self.params.get('base_red', 0),
            self.params.get('base_green', 0),
            self.params.get('base_blue', 20),
        ], dtype=np.float32)

        sparkle = np.array([
            self.params.get('sparkle_red', 255),
            self.params.get('sparkle_green', 255),
            self.params.get('sparkle_blue', 255),
        ], dtype=np.float32)

        sparkle_prob = self.params.get('sparkle_probability', 0.02)
        fade_speed = self.params.get('fade_speed', 0.9)

        if self.sparkle_brightness.shape[0] != total_pixels:
            self.sparkle_brightness = np.zeros(total_pixels, dtype=np.float32)
            self._color_scratch = np.empty((total_pixels, 3), dtype=np.float32)

        self.sparkle_brightness *= fade_speed
        self._emitted_last_frame = 0
        random_values = np.random.random(total_pixels)
        legacy_plant_aware = self._legacy_plant_aware()
        attractor = self.plant_modifier_enabled('attractor')
        repulsor = self.plant_modifier_enabled('repulsor')
        habitat = self.plant_modifier_enabled('habitat')
        emitter = self.plant_modifier_enabled('emitter')
        illuminate = self.plant_modifier_enabled('illuminate')
        plant_aware = legacy_plant_aware or attractor or repulsor or habitat or emitter or illuminate
        if legacy_plant_aware:
            masks = self.get_plant_masks()
            self._ensure_plant_field(masks)
            mask = random_values < sparkle_prob * self._plant_spawn_multiplier
            # A sparkle already present when the option is enabled should not
            # continue shining through an occluded core.
            self.sparkle_brightness[masks.obstacle_flat] = 0.0
        elif plant_aware:
            masks = self.get_plant_masks()
            field = 'attractor' if attractor else ('repulsor' if repulsor else '')
            self._ensure_modifier_field(
                masks,
                field,
                self.plant_modifier_strength(field) if field else 0.0,
                self.plant_modifier_strength('habitat') if habitat else 0.0,
            )
            mask = random_values < sparkle_prob * self._plant_spawn_multiplier
            if emitter:
                self._emit_from_plants(
                    random_values, sparkle_prob, time_elapsed,
                    self.plant_modifier_strength('emitter'),
                )
        else:
            # Keep the baseline comparison and RNG call exactly as before.
            mask = random_values < sparkle_prob
        self.sparkle_brightness[mask] = 1.0

        b = self.sparkle_brightness[:, np.newaxis]
        np.multiply(sparkle - base, b, out=self._color_scratch)
        self._color_scratch += base
        np.clip(self._color_scratch, 0, 255, out=self._color_scratch)
        result = self.next_frame_buffer(clear=False)
        result[:] = self._color_scratch
        self.apply_brightness_array(result, out=result)
        if legacy_plant_aware:
            self._accent_plant_cores(result)
        elif illuminate:
            strength = self.plant_modifier_strength('illuminate')
            if strength > 0.0:
                cores = masks.obstacle_flat
                lit = result[cores].astype(np.float32)
                lit *= np.float32(1.0 + 1.8 * strength)
                np.clip(lit, 0, 255, out=lit)
                result[cores] = lit.astype(np.uint8)
        return result

    def get_runtime_stats(self) -> Dict[str, Any]:
        legacy = self._legacy_plant_aware()
        active = [] if legacy else [
            modifier for modifier in self.plant_modifier_state().active
            if modifier in self.PLANT_MODIFIER_SUPPORT
        ]
        if not active and not legacy:
            return {'plant_aware': False, 'plant_modifiers': []}
        masks = self.get_plant_masks()
        return {
            'plant_aware': True,
            'plant_modifiers': active if active else ['legacy'],
            'plant_modifier_strengths': {
                modifier: self.plant_modifier_strength(modifier) for modifier in active
            },
            'plant_foliage_pixels': masks.foliage_count,
            'plant_globe_pixels': masks.globe_count,
            'plant_globe_regions': masks.globe_regions,
            'plant_mask_error': masks.error,
            'plant_emitted_last_frame': self._emitted_last_frame,
            'plant_emitted_total': self._emitted_total,
        }

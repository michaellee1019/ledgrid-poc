"""Smooth two-color gradients for the LED wall."""

from typing import Any, Dict, Optional

import numpy as np

from animation import AnimationBase
from animation.libraries.mask_effects import dilate_8
from animation.libraries.spatial import normalized_axis_positions


class GradientAnimation(AnimationBase):
    ANIMATION_NAME = "Color Gradient"
    ANIMATION_DESCRIPTION = "Two-color horizontal, vertical, or diagonal gradient"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "2.0"
    PLANT_MODIFIER_SUPPORT = frozenset(("illuminate", "shadow", "refract"))

    def __init__(self, controller, config: Optional[Dict[str, Any]] = None):
        super().__init__(controller, config)
        self.default_params.update({
            "color1_red": 255, "color1_green": 0, "color1_blue": 80,
            "color2_red": 0, "color2_green": 80, "color2_blue": 255,
            "direction": "vertical", "animated": False, "speed": 0.15,
            "plant_contour_strength": 0.85,
        })
        self.params = {**self.default_params, **self.config}
        self.params.pop("color_saturation", None)
        self.params.pop("color_value", None)
        self._plant_position_cache: Dict[tuple, np.ndarray] = {}
        self._plant_composition_cache: Dict[tuple, tuple] = {}
        self._scratch = np.empty((self.get_pixel_count(), 3), dtype=np.float32)
        self._last_static_key = None
        self._last_frame = None

    def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
        schema = super().get_parameter_schema()
        schema.pop("color_saturation", None)
        schema.pop("color_value", None)
        schema["speed"].update({
            "min": -2.0, "max": 2.0, "default": 0.15,
            "description": "Animated cycles per second; negative reverses",
        })
        schema.update({
            "color1_red": {"type": "int", "min": 0, "max": 255, "default": 255, "description": "First color red"},
            "color1_green": {"type": "int", "min": 0, "max": 255, "default": 0, "description": "First color green"},
            "color1_blue": {"type": "int", "min": 0, "max": 255, "default": 80, "description": "First color blue"},
            "color2_red": {"type": "int", "min": 0, "max": 255, "default": 0, "description": "Second color red"},
            "color2_green": {"type": "int", "min": 0, "max": 255, "default": 80, "description": "Second color green"},
            "color2_blue": {"type": "int", "min": 0, "max": 255, "default": 255, "description": "Second color blue"},
            "direction": {"type": "str", "default": "vertical", "description": "horizontal, vertical, or diagonal"},
            "animated": {"type": "bool", "default": False, "description": "Continuously cycle between both colors"},
            "plant_contour_strength": {
                "type": "float", "min": 0.0, "max": 1.0, "default": 0.85,
                "description": "How strongly gradient bands contour around foliage and globe silhouettes in plant-aware mode",
            },
        })
        return schema

    def update_parameters(self, new_params: Dict[str, Any]):
        super().update_parameters(new_params)
        self._last_static_key = None
        self._plant_position_cache.clear()
        self._plant_composition_cache.clear()

    def generate_frame(self, time_elapsed: float, frame_count: int):
        direction = str(self.params.get("direction", "vertical")).lower()
        if direction not in {"horizontal", "vertical", "diagonal"}:
            direction = "vertical"
        animated = bool(self.params.get("animated", False))
        legacy_plant_aware = self._legacy_plant_aware()
        illuminate = self.plant_modifier_enabled("illuminate")
        shadow = self.plant_modifier_enabled("shadow")
        refract = self.plant_modifier_enabled("refract")
        plant_aware = legacy_plant_aware or illuminate or shadow or refract
        first, second = self._color("color1"), self._color("color2")
        brightness = min(1.0, max(0.0, float(self.params.get("brightness", 1.0))))
        plant_key = ()
        if legacy_plant_aware:
            plant_key = (
                "legacy",
                float(self.params.get("plant_contour_strength", 0.85)),
                int(self.params.get("plant_clearance", 1)),
                str(self.params.get("plant_mask_path", "")),
                str(self.params.get("plant_globe_mask_path", "")),
            )
        elif plant_aware:
            state = self.plant_modifier_state()
            plant_key = (
                tuple(modifier for modifier in state.active
                      if modifier in self.PLANT_MODIFIER_SUPPORT),
                tuple((modifier, state.strengths[modifier]) for modifier in state.active
                      if modifier in self.PLANT_MODIFIER_SUPPORT),
                int(self.params.get("plant_clearance", 1)),
                str(self.params.get("plant_mask_path", "")),
                str(self.params.get("plant_globe_mask_path", "")),
            )
        static_key = None if animated else (
            direction, tuple(first), tuple(second), brightness, plant_aware, plant_key,
        )
        if not animated and static_key == self._last_static_key and self._last_frame is not None:
            return self.rendered_frame(self._last_frame, changed=False)

        if legacy_plant_aware:
            position = self._plant_positions(direction)
        elif refract:
            position = self._refracted_positions(
                direction, self.plant_modifier_strength("refract")
            )
        else:
            position = self._positions(direction)
        if animated:
            phase = position + float(time_elapsed) * float(self.params.get("speed", 0.15))
            blend = 0.5 - 0.5 * np.cos(phase * (2.0 * np.pi))
        else:
            blend = position
        np.multiply(second - first, blend[:, None], out=self._scratch)
        self._scratch += first
        if not legacy_plant_aware and (shadow or illuminate):
            self._compose_plant_light(shadow, illuminate)
        self._scratch *= brightness
        np.clip(self._scratch, 0, 255, out=self._scratch)
        frame = self.next_frame_buffer(clear=False)
        frame[:] = self._scratch
        self._last_static_key, self._last_frame = static_key, frame
        return self.rendered_frame(frame)

    def _legacy_plant_aware(self) -> bool:
        """Keep direct ``plant_aware=True`` construction byte-compatible."""
        payload = self.params.get("plant_modifiers")
        active = payload.get("active", ()) if isinstance(payload, dict) else ()
        return bool(self.params.get("plant_aware", False)) and not active

    def _refracted_positions(self, direction: str, strength: float) -> np.ndarray:
        masks = self.get_plant_masks()
        strength = min(1.0, max(0.0, float(strength)))
        cache_key = ("refract", direction, id(masks), strength)
        cached = self._plant_position_cache.get(cache_key)
        if cached is not None:
            return cached
        if strength <= 0.0 or not np.any(masks.obstacle):
            return self._positions(direction)

        base = self._positions(direction).reshape(masks.obstacle.shape)
        reach = 2.0 + strength * 8.0
        influence = np.clip(1.0 - masks.distance / reach, 0.0, 1.0)
        if direction == "horizontal":
            normal = masks.normal_x
        elif direction == "vertical":
            normal = -masks.normal_y
        else:
            normal = (masks.normal_x - masks.normal_y) * np.float32(0.5)
        # A bounded phase displacement bends bands around the calibrated
        # boundary without changing either endpoint color or simulation state.
        result = np.mod(
            base + normal * influence * np.float32(0.16 * strength), 1.0
        ).astype(np.float32, copy=False).ravel()
        self._plant_position_cache[cache_key] = result
        return result

    def _compose_plant_light(self, shadow: bool, illuminate: bool) -> None:
        masks = self.get_plant_masks()
        shadow_strength = self.plant_modifier_strength("shadow") if shadow else 0.0
        illuminate_strength = (
            self.plant_modifier_strength("illuminate") if illuminate else 0.0
        )
        cache_key = (id(masks), shadow_strength, illuminate_strength)
        cached = self._plant_composition_cache.get(cache_key)
        if cached is None:
            core = masks.obstacle_flat
            edge = masks.obstacle_edge.ravel()
            shadow_weight = np.zeros(core.shape, dtype=np.float32)
            illuminate_weight = np.zeros(core.shape, dtype=np.float32)
            if shadow_strength > 0.0:
                # Irregular foliage stays translucent while solid rooting
                # globes retain a stronger occluding core.
                shadow_weight[masks.foliage_flat] = np.float32(0.55 * shadow_strength)
                shadow_weight[masks.globes_flat] = np.float32(0.88 * shadow_strength)
                halo = (~core) & (masks.distance.ravel() <= 2.0 + 3.0 * shadow_strength)
                shadow_weight[halo] = np.float32(0.18 * shadow_strength)
            if illuminate_strength > 0.0:
                if shadow_strength > 0.0:
                    illuminate_weight[edge] = np.float32(illuminate_strength)
                    halo = (~core) & (masks.distance.ravel() <= 1.0 + 3.0 * illuminate_strength)
                    illuminate_weight[halo] = (
                        illuminate_strength
                        * np.clip(1.0 - masks.distance.ravel()[halo] / 5.0, 0.0, 1.0)
                    )
                else:
                    illuminate_weight[core] = np.float32(illuminate_strength)
                    halo = (~core) & (masks.distance.ravel() <= 1.0 + 3.0 * illuminate_strength)
                    illuminate_weight[halo] = (
                        illuminate_strength
                        * np.clip(1.0 - masks.distance.ravel()[halo] / 5.0, 0.0, 1.0)
                    )
            cached = (shadow_weight, illuminate_weight)
            self._plant_composition_cache[cache_key] = cached
        shadow_weight, illuminate_weight = cached
        if shadow_strength > 0.0:
            self._scratch *= (1.0 - shadow_weight[:, None])
        if illuminate_strength > 0.0:
            self._scratch *= (1.0 + 1.35 * illuminate_weight[:, None])

    def get_runtime_stats(self) -> Dict[str, Any]:
        legacy = self._legacy_plant_aware()
        active = [] if legacy else [
            modifier for modifier in self.plant_modifier_state().active
            if modifier in self.PLANT_MODIFIER_SUPPORT
        ]
        if not active and not legacy:
            return {"plant_modifiers": []}
        masks = self.get_plant_masks()
        return {
            "plant_modifiers": active if active else ["legacy"],
            "plant_modifier_strengths": {
                modifier: self.plant_modifier_strength(modifier) for modifier in active
            },
            "plant_foliage_pixels": masks.foliage_count,
            "plant_globe_pixels": masks.globe_count,
            "plant_mask_error": masks.error,
        }

    def _positions(self, direction: str) -> np.ndarray:
        width, height = self.get_strip_info()
        return normalized_axis_positions(width, height, direction)

    def _plant_positions(self, direction: str) -> np.ndarray:
        """Bend the phase field around the two calibrated semantic layers.

        Foliage holds the early part of the gradient while rooting globes hold
        the late part.  Soft, clearance-aware rings pull neighboring bands
        toward those anchors, making both silhouettes appear to shape the
        gradient rather than merely having color painted over them.
        """
        masks = self.get_plant_masks()
        strength = min(
            1.0, max(0.0, float(self.params.get("plant_contour_strength", 0.85)))
        )
        radius = min(10, max(1, int(self.params.get("plant_clearance", 1)) + 2))
        cache_key = (direction, id(masks), strength, radius)
        cached = self._plant_position_cache.get(cache_key)
        if cached is not None:
            return cached

        result = self._positions(direction).reshape(masks.obstacle.shape).copy()
        # Keep the semantic cores distinct even at strength zero: opting into
        # plant awareness should still reveal the calibrated subjects.
        # Half-cycle separation also keeps the layers visually distinct when
        # the gradient is animated through its cosine color cycle.
        layers = (
            (masks.foliage, np.float32(0.12)),
            (masks.globes, np.float32(0.62)),
        )
        for core, anchor in layers:
            if not np.any(core):
                continue
            reached = core.copy()
            frontier = core.copy()
            result[core] = anchor
            for distance in range(1, radius + 1):
                expanded = dilate_8(frontier)
                ring = expanded & ~reached
                weight = strength * (1.0 - distance / (radius + 1.0)) ** 1.5
                result[ring] += (anchor - result[ring]) * np.float32(weight)
                reached |= expanded
                frontier = expanded

        flattened = result.ravel()
        self._plant_position_cache[cache_key] = flattened
        return flattened

    def _color(self, prefix: str) -> np.ndarray:
        defaults = (255, 0, 80) if prefix == "color1" else (0, 80, 255)
        return np.asarray([
            max(0, min(255, int(self.params.get(f"{prefix}_{channel}", default))))
            for channel, default in zip(("red", "green", "blue"), defaults)
        ], dtype=np.float32)

"""Time-aware procedural sky with deterministic fixed-hour mode."""
import numpy as np

from animation.libraries.procedural_longform import LongformSceneBase

SEMANTIC_PALETTE_ROLES = ("background_low", "primary", "accent")


class CircadianWindowAnimation(LongformSceneBase):
    ANIMATION_NAME = "Circadian Window"
    ANIMATION_DESCRIPTION = "An all-day procedural sky moving through dawn, daylight, and night"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    SCENE = "circadian"
    DEFAULT_MOOD = "natural"
    MOODS = ("natural", "ember", "sleeper")

    def scene_defaults(self):
        return {"hour": -1.0, "time_offset": 0.0, "time_scale": 1.0}

    def scene_schema(self):
        return {
            "hour": {"type":"float","min":-1.0,"max":23.999,"default":-1.0,"description":"Fixed local hour; -1 uses the clock"},
            "time_offset": {"type":"float","min":-12.0,"max":14.0,"default":0.0,"description":"Local clock offset in hours"},
            "time_scale": {"type":"float","min":0.0,"max":3600.0,"default":1.0,"description":"Fixed-hour simulation time scale"},
        }

    def scene_key(self):
        return (float(self.params.get("hour",-1)), float(self.params.get("time_offset",0)), float(self.params.get("time_scale",1)))

    def palette(self):
        authored = super().palette()
        context = self.presentation_context
        if (
            context is None
            or context.vibe_id == "neutral"
            or self.VIBE_COLOR_POLICY != "semantic"
            or "palette_roles" not in self.VIBE_CAPABILITIES
        ):
            return authored
        return tuple(
            np.asarray(context.palette_roles.get(role, authored[index]), dtype=np.float32)
            for index, role in enumerate(SEMANTIC_PALETTE_ROLES)
        )

    def on_presentation_context_changed(self, old_context, new_context) -> None:
        """Invalidate only the rendered sky when presentation colors change."""
        self._last_key = None

    def _circadian_palette(self):
        context = self.presentation_context
        if (
            context is None
            or context.vibe_id == "neutral"
            or self.VIBE_COLOR_POLICY != "semantic"
            or "palette_roles" not in self.VIBE_CAPABILITIES
        ):
            return super()._circadian_palette()
        return self.palette()

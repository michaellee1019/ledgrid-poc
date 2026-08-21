"""Tall parallax night landscape viewed from a train."""
import numpy as np

from animation.libraries.procedural_longform import LongformSceneBase

SEMANTIC_PALETTE_ROLES = ("background_low", "primary", "accent")


class NightTrainWindowsAnimation(LongformSceneBase):
    ANIMATION_NAME = "Night Train Windows"
    ANIMATION_DESCRIPTION = "Moonlit terrain, poles, and warm towns pass at layered speeds"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    SCENE = "train"
    DEFAULT_MOOD = "sleeper"
    MOODS = ("sleeper", "moonlit", "ember")

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
        """Invalidate only the rendered landscape for a live vibe switch."""
        self._last_key = None

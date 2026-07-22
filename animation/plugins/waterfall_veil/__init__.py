"""Sparse full-height streams, ledges, pools, and mist."""

from animation.libraries.procedural_atmospheres import ProceduralAtmosphereBase


class WaterfallVeilAnimation(ProceduralAtmosphereBase):
    ANIMATION_NAME = "Waterfall Veil"
    ANIMATION_DESCRIPTION = "Fine streams descend through slate ledges and break into luminous mist"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    SCENE = "waterfall"
    DEFAULT_MOOD = "garden"
    DEFAULT_SEED = 4401
    PLANT_MODIFIER_SUPPORT = frozenset({"shadow", "illuminate", "emitter"})

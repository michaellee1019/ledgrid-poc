"""Slow fog banks enclosing a vertical moonlit canyon."""

from animation.libraries.procedural_atmospheres import ProceduralAtmosphereBase


class CloudCanyonAnimation(ProceduralAtmosphereBase):
    PREFERRED_PARAMETERS = ('density', 'motion', 'background_level')
    INTERACTION_TYPES = frozenset({"primary"})
    POINT_ACTION_LABELS = {"primary": "Light a cloud break"}
    NIMBUS_POINT_BINDINGS = {"primary": ("a", 3)}
    POINT_PULSE_SECONDS = 1.1

    ANIMATION_NAME = "Cloud Canyon"
    ANIMATION_DESCRIPTION = "Billowing cloud banks occlude broad shafts leaking through a tall canyon"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    SCENE = "cloud"
    DEFAULT_MOOD = "moonlit"
    DEFAULT_SEED = 4301
    PLANT_MODIFIER_SUPPORT = frozenset({"shadow", "refract", "emitter"})
    COMPONENT_ID = "cloud_canyon"
    SCENE_SEMANTIC_PALETTE = True
    COMPONENT_DEFAULTS = {
        "motion": .42, "density": .46, "mood": "moonlit", "background": "soft",
        "background_level": .18, "source_fps": 30.0, "seed": 4301,
    }

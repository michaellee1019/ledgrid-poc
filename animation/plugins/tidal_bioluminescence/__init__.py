"""Dark tide with sparse plankton wakes."""

from animation.libraries.procedural_atmospheres import ProceduralAtmosphereBase


class TidalBioluminescenceAnimation(ProceduralAtmosphereBase):
    PREFERRED_PARAMETERS = ('density', 'motion', 'background_level')
    INTERACTION_TYPES = frozenset({"primary"})
    POINT_ACTION_LABELS = {"primary": "Stir a glowing wake"}
    NIMBUS_POINT_BINDINGS = {"primary": ("a", 3)}
    POINT_PULSE_SECONDS = .9
    LIVE_TUNABLE_PARAMETERS = frozenset({"motion", "density", "mood", "background", "background_level", "seed"})

    ANIMATION_NAME = "Tidal Bioluminescence"
    ANIMATION_DESCRIPTION = "Slow swells disturb sparse plankton into curling cyan wakes"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    SCENE = "tidal"
    DEFAULT_MOOD = "moonlit"
    DEFAULT_SEED = 4501
    PLANT_MODIFIER_SUPPORT = frozenset({"refract", "illuminate", "emitter"})
    COMPONENT_ID = "tidal_bioluminescence"
    SCENE_SEMANTIC_PALETTE = True
    COMPONENT_DEFAULTS = {
        "motion": .42, "density": .46, "mood": "moonlit", "background": "soft",
        "background_level": .18, "source_fps": 30.0, "seed": 4501,
    }

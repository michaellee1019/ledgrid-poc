"""Time-aware procedural sky with deterministic fixed-hour mode."""
from animation.libraries.procedural_longform import LongformSceneBase
from animation.libraries.point_pulse import AimedPoint

class CircadianWindowAnimation(LongformSceneBase):
    PREFERRED_PARAMETERS = ('hour', 'motion', 'density', 'background_level')
    INTERACTION_TYPES = frozenset({"primary"})
    POINT_ACTION_LABELS = {"primary": "Preview time or reset at far right"}
    POINT_ACTION_AXES = {"primary": ("x",)}
    NIMBUS_POINT_BINDINGS = {"primary": ("a", 3)}
    POINT_PULSE_SECONDS = 3.0
    PARAMETER_CONTROLS = {
        'hour': {'label': 'Fixed hour or clock (-1)', 'unit': 'h', 'step': .25},
        'time_offset': {'label': 'Clock offset', 'unit': 'h', 'step': .25,
                        'visible_when': {'parameter': 'hour', 'below': 0}},
        'time_scale': {'label': 'Fixed time rate', 'unit': '×', 'step': 60, 'coarse_step': 300,
                       'visible_when': {'parameter': 'hour', 'at_least': 0}},
    }

    ANIMATION_NAME = "Circadian Window"
    ANIMATION_DESCRIPTION = "An all-day procedural sky moving through dawn, daylight, and night"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    SCENE = "circadian"
    DEFAULT_MOOD = "natural"
    MOODS = ("natural", "ember", "sleeper")
    COMPONENT_ID = "circadian_window"
    SCENE_SEMANTIC_PALETTE = True
    COMPONENT_DEFAULTS = {
        "motion": .35, "density": .5, "mood": "natural", "background": "soft",
        "background_level": .18, "render_fps": 24, "seed": 29001,
        "hour": -1.0, "time_offset": 0.0, "time_scale": 1.0,
    }

    def scene_defaults(self):
        return {"hour": -1.0, "time_offset": 0.0, "time_scale": 1.0}

    @classmethod
    def scene_schema(cls):
        return {
            "hour": {"type":"float","min":-1.0,"max":23.999,"default":-1.0,"description":"Fixed local hour; -1 uses the clock"},
            "time_offset": {"type":"float","min":-12.0,"max":14.0,"default":0.0,"description":"Local clock offset in hours"},
            "time_scale": {"type":"float","min":0.0,"max":3600.0,"default":1.0,"description":"Fixed-hour simulation time scale"},
        }

    def scene_key(self):
        return (float(self.params.get("hour",-1)), float(self.params.get("time_offset",0)), float(self.params.get("time_scale",1)))

    def handle_interaction(self, kind, x, y, strength=1.0):
        point = AimedPoint.from_input(kind, x, y, strength, self.width, self.height)
        if point is None:
            return False
        if point.x == self.width - 1:
            return self._pulse.clear()
        return self._pulse.trigger(point)

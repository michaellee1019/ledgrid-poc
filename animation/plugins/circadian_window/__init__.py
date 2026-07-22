"""Time-aware procedural sky with deterministic fixed-hour mode."""
from animation.libraries.procedural_longform import LongformSceneBase

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


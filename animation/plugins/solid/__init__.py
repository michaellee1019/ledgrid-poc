"""Scene v2 Solid ambient instrument."""
from dataclasses import replace
from types import MappingProxyType

import numpy as np

from animation.core.component_catalog import ComponentDescriptor
from animation.core.compositing import BaseFrame
from animation.libraries.ambient_scene import AmbientSceneAnimation
class SolidColorAnimation(AmbientSceneAnimation):
    PREFERRED_PARAMETERS = ('glow', 'breath')
    PARAMETER_CONTROLS = {'breath': {'step': .01, 'coarse_step': .05, 'unit': 'Hz'}, 'seed': {'control': 'fixed'}}

    ANIMATION_NAME="Solid"; ANIMATION_DESCRIPTION="A calm semantic wash with optional breath"; ANIMATION_VERSION="2.0"; COMPONENT_ID="solid"; STYLE="solid"
    DEFAULTS=MappingProxyType({"glow":.68,"breath":.0,"seed":6103})
    SCHEMA={"glow":("float",0.,1.,"Field fullness, zero is dark"),"breath":("float",0.,3.,"Gentle breathing tempo"),"seed":("int",0,999999,"Repeatable field seed")}

    def presentation_rgb(self, rgb, params):
        if params["glow"] == 0:
            rgb.fill(0)
        return rgb


    @classmethod
    def background_descriptor(cls):
        return ComponentDescriptor(
            component_id="solid_background", version=1, provider="python", role="background",
            timing_policy="scaled_context", alpha_behavior="none", palette_policy="semantic",
            plant_capabilities=("final_optics",), fidelity_exceptions=(),
            intensity_parameter="gain", defaults={"gain": 0.62, "seed": 4201},
            parameter_schema={
                "gain": {"type": "float", "min": 0, "max": 1, "default": .62,
                         "step": .05, "label": "Background light"},
                "seed": {"type": "int", "min": 0, "max": 999999, "default": 4201, "step": 1,
                         "label": "Stored seed", "control": "fixed"},
            },
            name="Solid background", description="A steady semantic wash behind the Animation.",
            parameter_normalizer=cls._normalize_background, preserve_parameter_encoding=True,
            factory=SolidBackground,
        )

    @classmethod
    def _normalize_background(cls, values):
        unknown = values.keys() - {"gain", "seed"}
        if unknown:
            raise ValueError(f"Solid background does not accept non-local parameters: {sorted(unknown)!r}")
        cls._normalized_parameters({"seed": values["seed"]})
        if values["gain"] is None:
            raise ValueError("gain must be a number from 0 to 1")
        return values


class SolidBackground:
    """Render the authored Scene wash using the Solid field and its palette."""

    def __init__(self, controller, parameters):
        self._wash_parameters = {"glow": .68, "breath": 0, "seed": parameters["seed"]}
        self._renderer = SolidColorAnimation(controller, self._wash_parameters)
        self._gain = parameters["gain"]
        self._palette_id = None
        self._pixels = None

    def render_background(self, context, frame_count):
        palette_id = context.palette["palette_id"]
        if palette_id == self._palette_id:
            return BaseFrame(self._pixels, changed=False, dirty_ranges=())
        rendered = self._renderer.render_resolved_scene(replace(
            context, descriptor=self._renderer.component_descriptor(), parameters=self._wash_parameters,
        ))
        self._pixels = np.rint(rendered.pixels.astype(np.float32) * self._gain).astype(np.uint8)
        self._palette_id = palette_id
        return BaseFrame(self._pixels, changed=True)

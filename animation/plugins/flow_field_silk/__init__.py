"""Coherent luminous filaments advected through an analytic curl field."""
from functools import lru_cache

import numpy as np

from animation.core.plant_awareness import INSTALLATION_GEOMETRY_CONTACT_INPUT
from animation.libraries.procedural_sculptures import CadencedSculpture


class FlowFieldSilkAnimation(CadencedSculpture):
    ANIMATION_NAME = "Flow-Field Silk"
    ANIMATION_DESCRIPTION = "Fine advected threads braid and fray in a slow underwater vector field"
    # Refraction is deliberately presentation-only: the provider supplies the
    # calibrated contact view and this renderer never lets it steer a thread.
    PLANT_MODIFIER_SUPPORT = frozenset(("refract",))
    SOURCE_FPS = 30.0
    COMPONENT_ID = "flow_field_silk"
    COMPONENT_DEFAULTS = {"motion": .52, "density": .50, "background_level": .14, "seed": 1901,
                          "turbulence": .35, "persistence": .80}

    def __init__(self, controller, config=None):
        super().__init__(controller, config); self._init_threads()
        self._edge_cache = None
        self._geometry_identity = None

    @classmethod
    @lru_cache(maxsize=None)
    def component_descriptor(cls):
        descriptor = super().component_descriptor()
        return type(descriptor)(
            component_id=descriptor.component_id, version=descriptor.version,
            provider=descriptor.provider, role=descriptor.role,
            timing_policy=descriptor.timing_policy,
            alpha_behavior=descriptor.alpha_behavior,
            palette_policy=descriptor.palette_policy,
            plant_capabilities=("effect_intent", "simulation_inputs"),
            fidelity_exceptions=descriptor.fidelity_exceptions,
            optional_simulation_inputs=(INSTALLATION_GEOMETRY_CONTACT_INPUT,),
            defaults=descriptor.defaults,
            parameter_normalizer=descriptor.parameter_normalizer,
        )

    def _init_threads(self):
        n = max(4, int(8 + 28 * float(self.params["density"])))
        length = 22
        heads = np.column_stack((self.rng.uniform(0, self._shape[0], n), self.rng.uniform(0, self._shape[1], n)))
        self.filaments = np.repeat(heads[:, None, :], length, axis=1).astype(np.float32)

    def get_parameter_schema(self):
        s=super().get_parameter_schema(); s.update({
            "turbulence":{"type":"float","min":0,"max":1,"default":0.35,"description":"Curl-field complexity"},
            "persistence":{"type":"float","min":0.1,"max":1,"default":0.8,"description":"Visible filament trail length"},
        }); return s

    def update_parameters(self, params):
        old=float(self.params.get("density",.5)); super().update_parameters(params)
        if "density" in params and float(params["density"]) != old: self._init_threads()

    @classmethod
    def _validate_local_parameters(cls, values):
        super()._validate_local_parameters(values)
        for name, low, high in (("turbulence", 0., 1.), ("persistence", .1, 1.)):
            if not low <= float(values[name]) <= high: raise ValueError(f"{name} is out of range")

    def _step(self, tick):
        self.filaments[:,1:] = self.filaments[:,:-1]
        p=self.filaments[:,0]; x=p[:,0]/self._shape[0]*6.28; y=p[:,1]/self._shape[1]*6.28
        phase=tick*.018*float(self.params["motion"]); turb=float(self.params["turbulence"])
        vx=np.sin(y*.75+phase)+turb*np.cos(x*1.7-phase*.6)
        vy=.8+0.6*np.cos(x*.8-phase)+turb*np.sin(y*1.3+phase)
        p[:,0]=(p[:,0]+vx*.16*(.3+float(self.params["motion"])))%self._shape[0]
        p[:,1]=(p[:,1]+vy*.16*(.3+float(self.params["motion"])))%self._shape[1]

    def reset_simulation(self): super().reset_simulation(); self._init_threads()

    def palette(self, mood=None):
        return super().palette(mood)

    def on_presentation_context_changed(self, _old, _new):
        self._render_key = None
        self._cached_pixels = None

    def set_presentation_context(self, context):
        """Discard only derived edge paint when the provider swaps geometry."""
        before = self._geometry_identity
        super().set_presentation_context(context)
        contact = getattr(self._presentation_context, "installation_geometry", None)
        identity = None if contact is None else (contact.identity, contact.available, contact.status)
        if identity != before:
            self._edge_cache = None
            # An inactive effect has not consumed geometry into the source
            # frame, so its source-rate cache remains exactly reusable.
            if self._refract_strength() > 0.0:
                self._render_key = None
                self._cached_pixels = None
        self._geometry_identity = identity

    def _refract_strength(self):
        context = self._presentation_context
        if context is None:
            return 0.0
        effects = context.canonical_scene.get("plants", {}).get("effects", {})
        if "refract" not in effects.get("active", ()):
            return 0.0
        strength = effects.get("strengths", {}).get("refract", 0.5)
        return float(strength) if isinstance(strength, (int, float)) else 0.0

    def _refraction_cache(self):
        """Return a bounded cached sampling field from immutable provider data."""
        contact = getattr(self._presentation_context, "installation_geometry", None)
        if contact is None or not contact.available or contact.geometry.obstacle_edge.shape != self._shape:
            return None
        key = (contact.identity, self._shape)
        if self._edge_cache is not None and self._edge_cache[0] == key:
            return self._edge_cache[1]
        geometry = contact.geometry
        # The exact union edge anchors the band; distance and normals merely
        # describe its bounded presentation falloff and sampling direction.
        distance = geometry.distance.astype(np.float32, copy=False)
        band = np.clip(1.0 - distance / 3.0, 0.0, 1.0)
        band = np.maximum(band, geometry.obstacle_edge.astype(np.float32))
        grid_x, grid_y = np.indices(self._shape)
        sample_x = np.clip(grid_x - np.rint(geometry.normal_x * 2.0).astype(int), 0, self._shape[0] - 1)
        sample_y = np.clip(grid_y - np.rint(geometry.normal_y * 2.0).astype(int), 0, self._shape[1] - 1)
        cached = (band, sample_x, sample_y)
        self._edge_cache = (key, cached)
        return cached

    def _apply_edge_refraction(self, rgb):
        strength = self._refract_strength()
        if strength <= 0.0:
            return rgb
        cached = self._refraction_cache()
        if cached is None:
            return rgb
        band, sample_x, sample_y = cached
        blend = np.clip(band * strength, 0.0, 1.0)[..., None]
        return rgb * (1.0 - blend) + rgb[sample_x, sample_y] * blend

    def generate_frame(self,time_elapsed,frame_count):
        tick,cached=self.begin_frame(time_elapsed)
        if cached:return cached
        self.advance_bounded(tick,self._step)
        value=np.zeros(self._shape,np.float32); accent=np.zeros_like(value)
        keep=max(3,int(self.filaments.shape[1]*float(self.params["persistence"])))
        for age in range(keep):
            pts=self.filaments[:,age]; ix=np.clip(pts[:,0].astype(int),0,self._shape[0]-1); iy=np.clip(pts[:,1].astype(int),0,self._shape[1]-1)
            np.maximum.at(value,(ix,iy),(1-age/keep)*.9); np.maximum.at(accent,(ix,iy),(1-age/keep)*.6)
        return self.finish_frame(tick,self._apply_edge_refraction(self.colorize(value,accent)))

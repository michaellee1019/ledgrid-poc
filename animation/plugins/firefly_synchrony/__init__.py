"""Locally coupled wandering fireflies with a hard collective peak cap."""

from __future__ import annotations

import numpy as np

from animation.libraries.procedural_living import ProceduralLivingBase


class FireflySynchronyAnimation(ProceduralLivingBase):
    ANIMATION_NAME = "Firefly Synchrony"
    ANIMATION_DESCRIPTION = "Wandering coupled oscillators assemble and lose local rhythms"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    PLANT_MODIFIER_SUPPORT = frozenset(("habitat","attractor","repulsor","emitter","slow_zone","illuminate"))
    SIM_HZ = 15.0
    MAX_FIREFLIES = 220
    MAX_PEAK_FRACTION = .18

    def __init__(self,controller,config=None):
        super().__init__(controller,config)
        self.default_params.update({"population":100,"coupling_radius":8.0,"synchrony":.85,"wandering":.55,
                                    "pulse_softness":.5,"meadow_glow":.12})
        self.params={**self.default_params,**self.config}; self.rng=np.random.default_rng(int(self.params["seed"])); self._initialize_simulation()

    def get_parameter_schema(self):
        s=super().get_parameter_schema(); s.update({
            "population":{"type":"int","min":20,"max":self.MAX_FIREFLIES,"default":100,"description":"Bounded oscillator population"},
            "coupling_radius":{"type":"float","min":2.0,"max":18.0,"default":8.0,"description":"Local synchronization radius"},
            "synchrony":{"type":"float","min":0.0,"max":2.0,"default":.85,"description":"Kuramoto coupling strength"},
            "wandering":{"type":"float","min":0.0,"max":2.0,"default":.55,"description":"Semantic drift strength"},
            "pulse_softness":{"type":"float","min":.1,"max":1.0,"default":.5,"description":"Presentation-only flash width"},
            "meadow_glow":{"type":"float","min":0.0,"max":.5,"default":.12,"description":"Presentation-only ground glow"},
        }); return s

    def update_parameters(self,new_params):
        structural="population" in new_params; super().update_parameters(new_params)
        if structural: self.rng=np.random.default_rng(int(self.params["seed"])); self._initialize_simulation()

    def _initialize_simulation(self):
        n=max(10,min(self.MAX_FIREFLIES,int(self.params.get("population",100)*float(np.clip(self.params.get("density",1),.2,2)))))
        self.x=self.rng.uniform(0,self.width,n).astype(np.float32); self.y=self.rng.uniform(0,self.height,n).astype(np.float32)
        self.vx=self.rng.normal(0,.22,n).astype(np.float32); self.vy=self.rng.normal(0,.22,n).astype(np.float32)
        self.phase=self.rng.uniform(0,2*np.pi,n).astype(np.float32); self.frequency=self.rng.normal(2*np.pi*.72,.14,n).astype(np.float32)
        self.energy=np.ones(n,dtype=np.float32); self._last_peak_count=0

    def _simulate_step(self,dt):
        dx=self.x[:,None]-self.x[None,:]; dx-=np.rint(dx/self.width)*self.width
        dy=self.y[:,None]-self.y[None,:]
        radius=float(self.params.get("coupling_radius",8)); near=(dx*dx+dy*dy<radius*radius); np.fill_diagonal(near,False)
        differences=self.phase[None,:]-self.phase[:,None]
        coupling=(np.sin(differences)*near).sum(axis=1)/np.maximum(1,near.sum(axis=1))
        self.phase=np.mod(self.phase+(self.frequency+float(self.params.get("synchrony",.85))*coupling)*dt,2*np.pi)
        phase_distance=np.minimum(self.phase,2*np.pi-self.phase)
        self.energy += dt*.035 - (phase_distance < .28).astype(np.float32)*dt*.18
        wander=float(self.params.get("wandering",.55)); noise=self.rng.normal(0,.08,self.x.size).astype(np.float32)
        self.vx+=np.cos(self.phase*.37)*wander*dt*.12+noise*dt; self.vy+=np.sin(self.phase*.31)*wander*dt*.12-noise*dt
        field="attractor" if self.plant_modifier_strength("attractor")>0 else ("repulsor" if self.plant_modifier_strength("repulsor")>0 else "")
        slow=self.plant_modifier_strength("slow_zone")
        if field or slow>0:
            masks=self.get_plant_masks(); ix=np.mod(self.x.astype(int),self.width); iy=np.clip(self.y.astype(int),0,self.height-1)
            if field:
                strength=self.plant_modifier_strength(field); sign=-1 if field=="attractor" else 1
                self.vx+=sign*masks.normal_y.T[iy,ix]*strength*dt; self.vy+=sign*masks.normal_x.T[iy,ix]*strength*dt
            if slow>0:
                factor=1-masks.clearance.T[iy,ix].astype(np.float32)*(.75*slow); self.vx*=factor; self.vy*=factor
        np.clip(self.vx,-.8,.8,out=self.vx); np.clip(self.vy,-.8,.8,out=self.vy)
        self.x=np.mod(self.x+self.vx,self.width); self.y+=self.vy
        bounce=(self.y<0)|(self.y>=self.height); self.vy[bounce]*=-1; np.clip(self.y,0,self.height-1,out=self.y)
        habitat=self.plant_modifier_strength("habitat")
        if habitat>0:
            masks=self.get_plant_masks(); ix=self.x.astype(int); iy=self.y.astype(int); self.energy+=masks.foliage.T[iy,ix].astype(np.float32)*dt*.1*habitat
        emitter=self.plant_modifier_strength("emitter")
        if emitter>0 and self._logical_generation%max(15,int(70-45*emitter))==0:
            edge=np.flatnonzero(self.get_plant_masks().obstacle_edge.T)
            if edge.size:
                m=min(5,self.x.size,edge.size); chosen=self.rng.choice(edge,m,replace=False); ey,ex=np.unravel_index(chosen,(self.height,self.width)); self.x[:m]=ex; self.y[:m]=ey; self.phase[:m]=self.rng.uniform(0,.3,m)
        np.clip(self.energy,0,1,out=self.energy)

    def _render_scene(self,elapsed):
        dark,mid,light=(np.asarray(c,dtype=np.float32) for c in self._palette())
        canvas=np.empty((self.height,self.width,3),dtype=np.uint8); canvas[:]=np.clip(dark+np.asarray(mid)*float(self.params.get("meadow_glow",.12))*(np.linspace(0,1,self.height)[:,None,None]**3),0,255).astype(np.uint8)
        softness=float(self.params.get("pulse_softness",.5)); distance=np.minimum(self.phase,2*np.pi-self.phase)
        pulse=np.exp(-(distance/(.16+.65*softness))**2)*self.energy
        candidates=np.flatnonzero(pulse>.72); cap=max(1,int(self.x.size*self.MAX_PEAK_FRACTION))
        if candidates.size>cap:
            keep=candidates[np.argsort(pulse[candidates])[-cap:]]; muted=np.ones(self.x.size,dtype=bool); muted[keep]=False; pulse[muted & (pulse>.72)]=.72
        self._last_peak_count=min(candidates.size,cap)
        for i in np.flatnonzero(pulse>.025):
            x=int(self.x[i])%self.width; y=int(np.clip(self.y[i],0,self.height-1)); c=mid+(light-mid)*pulse[i]; canvas[y,x]=np.maximum(canvas[y,x],np.clip(c,0,255).astype(np.uint8))
            if pulse[i]>.55:
                for oy,ox in ((-1,0),(1,0),(0,-1),(0,1)):
                    yy=y+oy; xx=(x+ox)%self.width
                    if 0<=yy<self.height: canvas[yy,xx]=np.maximum(canvas[yy,xx],np.clip(c*.25,0,255).astype(np.uint8))
        illuminate=self.plant_modifier_strength("illuminate")
        if illuminate>0 and self._last_peak_count:
            edge=self.get_plant_masks().obstacle_edge.T; canvas[edge]=np.maximum(canvas[edge],(light*(.08+.18*illuminate)).astype(np.uint8))
        return self._finish_canvas(canvas)

    def logical_state(self):
        return (self.x.tobytes(),self.y.tobytes(),self.vx.tobytes(),self.vy.tobytes(),self.phase.tobytes(),self.energy.tobytes())

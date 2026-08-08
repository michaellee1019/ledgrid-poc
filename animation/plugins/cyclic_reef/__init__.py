"""Vectorized cyclic cellular reef with bounded channel-grazing agents."""

from __future__ import annotations

import colorsys
import numpy as np

from animation.libraries.procedural_living import ProceduralLivingBase


class CyclicReefAnimation(ProceduralLivingBase):
    ANIMATION_NAME = "Cyclic Reef"
    ANIMATION_DESCRIPTION = "Competing cyclic species form waves while grazers open dark channels"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    PLANT_MODIFIER_SUPPORT = frozenset(("obstacle","habitat","hazard","emitter"))
    SIM_HZ = 8.0

    def __init__(self,controller,config=None):
        super().__init__(controller,config)
        self.default_params.update({"state_count":5,"threshold":2,"mutation":.002,"grazer_density":.5,
                                    "edge_glow":.55,"topology":"wrap"})
        self.params={**self.default_params,**self.config}; self.rng=np.random.default_rng(int(self.params["seed"])); self._initialize_simulation()

    def get_parameter_schema(self):
        s=super().get_parameter_schema(); s.update({
            "state_count":{"type":"int","min":3,"max":8,"default":5,"description":"Number of cyclic species"},
            "threshold":{"type":"int","min":1,"max":5,"default":2,"description":"Neighbors required for takeover"},
            "mutation":{"type":"float","min":0.0,"max":.02,"default":.002,"description":"Rare local species mutation"},
            "grazer_density":{"type":"float","min":0.0,"max":2.0,"default":.5,"description":"Bounded mobile disruptor population"},
            "edge_glow":{"type":"float","min":0.0,"max":1.5,"default":.55,"description":"Presentation-only species boundary glow"},
            "topology":{"type":"str","default":"wrap","options":["wrap","closed"],"description":"Neighborhood edge topology"},
        }); return s

    def update_parameters(self,new_params):
        structural=bool({"state_count","grazer_density","topology"}&new_params.keys()); super().update_parameters(new_params)
        if structural: self.rng=np.random.default_rng(int(self.params["seed"])); self._initialize_simulation()

    def _initialize_simulation(self):
        nstates=int(np.clip(self.params.get("state_count",5),3,8)); self.state=self.rng.integers(0,nstates,(self.height,self.width),dtype=np.uint8)
        # Dark cavities are an explicit reef state and never participate as a species.
        cavity=self.rng.random(self.state.shape)>.93; self.state[cavity]=255
        self.previous=self.state.copy(); self.age=np.zeros(self.state.shape,dtype=np.float32)
        ng=max(0,min(40,int(8*float(self.params.get("density",1))*float(self.params.get("grazer_density",.5)))))
        self.gx=self.rng.integers(0,self.width,ng,dtype=np.int16); self.gy=self.rng.integers(0,self.height,ng,dtype=np.int16)

    def _shift(self,a,dy,dx):
        shifted=np.roll(np.roll(a,dy,0),dx,1)
        if self.params.get("topology","wrap")=="closed":
            if dy==1: shifted[0,:]=255
            elif dy==-1: shifted[-1,:]=255
            if dx==1: shifted[:,0]=255
            elif dx==-1: shifted[:,-1]=255
        return shifted

    def _simulate_step(self,dt):
        nstates=int(np.clip(self.params.get("state_count",5),3,8)); successor=(self.state.astype(np.int16)+1)%nstates
        valid=self.state!=255; count=np.zeros(self.state.shape,dtype=np.uint8)
        for dy,dx in ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)):
            count+=(self._shift(self.state,dy,dx)==successor)
        threshold=int(np.clip(self.params.get("threshold",2),1,5)); habitat=self.plant_modifier_strength("habitat")
        local_threshold=np.full(self.state.shape,threshold,dtype=np.uint8)
        if habitat>0: local_threshold[self.get_plant_masks().foliage.T]=max(1,threshold-int(np.ceil(habitat)))
        advance=valid&(count>=local_threshold); self.previous[:]=self.state; self.state[advance]=successor[advance].astype(np.uint8)
        changed=self.state!=self.previous; self.age[changed]=0; self.age[~changed]+=dt
        mutation=float(self.params.get("mutation",.002)); mutate=(self.rng.random(self.state.shape)<mutation)&valid
        self.state[mutate]=(self.state[mutate]+1)%nstates
        if self.gx.size:
            self.gx=np.mod(self.gx+self.rng.integers(-1,2,self.gx.size),self.width).astype(np.int16)
            self.gy=np.clip(self.gy+self.rng.integers(-1,2,self.gy.size),0,self.height-1).astype(np.int16)
            self.state[self.gy,self.gx]=255
            # Bounded recolonization behind a moving grazer.
            restore=(self.rng.random(self.state.shape)<.006)&(self.state==255)
            self.state[restore]=self.rng.integers(0,nstates,int(restore.sum()),dtype=np.uint8)
        obstacle=self.plant_modifier_strength("obstacle"); hazard=self.plant_modifier_strength("hazard")
        if obstacle>0: self.state[self.get_plant_masks().obstacle.T]=255
        if hazard>0:
            mask=self.get_plant_masks().clearance.T; burn=(self.rng.random(self.state.shape)<(.018*hazard))&mask; self.state[burn]=255
        emitter=self.plant_modifier_strength("emitter")
        if emitter>0 and self._logical_generation%max(6,int(32-20*emitter))==0:
            edge=np.flatnonzero(self.get_plant_masks().obstacle_edge.T)
            if edge.size:
                chosen=self.rng.choice(edge,size=min(16,edge.size),replace=False); self.state.ravel()[chosen]=np.arange(chosen.size,dtype=np.uint8)%nstates

    def _render_scene(self,elapsed):
        nstates=int(np.clip(self.params.get("state_count",5),3,8)); mood=self.params.get("mood","reef")
        hue0={"reef":.48,"moon":.52,"violet":.72,"ember":.02,"dusk":.1}.get(mood,.48)
        palette=np.array([[int(c*255) for c in colorsys.hsv_to_rgb((hue0+i/nstates*.48)%1,.72,.56)] for i in range(nstates)],dtype=np.float32)
        canvas=np.zeros((self.height,self.width,3),dtype=np.float32)
        valid=self.state!=255; canvas[valid]=palette[self.state[valid]]
        boundary=np.zeros(self.state.shape,dtype=bool)
        for dy,dx in ((1,0),(-1,0),(0,1),(0,-1)): boundary|=self._shift(self.state,dy,dx)!=self.state
        canvas[boundary&valid]+=np.asarray((70,75,80),dtype=np.float32)*float(self.params.get("edge_glow",.55))
        # Presentation-only gentle interpolation fades newly changed polyps in.
        blend=np.clip(self.age/.25,0,1); old_valid=self.previous!=255
        old=np.zeros_like(canvas); old[old_valid]=palette[self.previous[old_valid]]
        canvas=old*(1-blend[...,None])+canvas*blend[...,None]
        return self._finish_canvas(np.clip(canvas,0,255).astype(np.uint8))

    def logical_state(self):
        return (self.state.tobytes(),self.age.tobytes(),self.gx.tobytes(),self.gy.tobytes())

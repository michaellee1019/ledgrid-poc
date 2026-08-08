"""Bounded particle slime mold reinforcing and pruning a pheromone network."""

from __future__ import annotations

import numpy as np

from animation.libraries.procedural_living import ProceduralLivingBase


class PhysarumNetworkAnimation(ProceduralLivingBase):
    ANIMATION_NAME = "Physarum Network"
    ANIMATION_DESCRIPTION = "Explorer agents reinforce efficient glowing routes between moving nutrients"
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    PLANT_MODIFIER_SUPPORT = frozenset(("attractor", "repulsor", "obstacle", "habitat", "emitter"))
    SIM_HZ = 15.0
    MAX_AGENTS = 1800

    def __init__(self, controller, config=None):
        super().__init__(controller, config)
        self.default_params.update({"agent_count":700,"branching":.75,"diffusion":.62,
                                    "nutrient_layout":"constellation","pulse_visibility":.4})
        self.params = {**self.default_params, **self.config}
        self.rng = np.random.default_rng(int(self.params["seed"]))
        self._initialize_simulation()

    def get_parameter_schema(self):
        s=super().get_parameter_schema(); s.update({
            "agent_count":{"type":"int","min":100,"max":self.MAX_AGENTS,"default":700,"description":"Capped explorer population"},
            "branching":{"type":"float","min":.1,"max":1.5,"default":.75,"description":"Sensor steering breadth"},
            "diffusion":{"type":"float","min":.1,"max":.9,"default":.62,"description":"Pheromone diffusion rate"},
            "nutrient_layout":{"type":"str","default":"constellation","options":["constellation","ladder","rings"],"description":"Adaptive resource arrangement"},
            "pulse_visibility":{"type":"float","min":0.0,"max":1.0,"default":.4,"description":"Presentation-only material pulse"},
        }); return s

    def update_parameters(self,new_params):
        structural=bool({"agent_count","nutrient_layout"}&new_params.keys()); super().update_parameters(new_params)
        if structural:
            self.rng=np.random.default_rng(int(self.params["seed"])); self._initialize_simulation()

    def _initialize_simulation(self):
        requested=int(self.params.get("agent_count",700)*float(np.clip(self.params.get("density",1),.2,2)))
        n=max(50,min(self.MAX_AGENTS,requested))
        self.x=self.rng.uniform(0,self.width,n).astype(np.float32)
        self.y=self.rng.uniform(0,self.height,n).astype(np.float32)
        self.heading=self.rng.uniform(-np.pi,np.pi,n).astype(np.float32)
        self.trail=np.zeros((self.height,self.width),dtype=np.float32)
        count=max(4,min(12,int(6*float(self.params.get("density",1)))))
        layout=self.params.get("nutrient_layout","constellation")
        if layout=="ladder":
            self.nutrient_x=np.where(np.arange(count)%2, self.width*.72,self.width*.28).astype(np.float32)
            self.nutrient_y=np.linspace(self.height*.1,self.height*.9,count).astype(np.float32)
        elif layout=="rings":
            a=np.linspace(0,2*np.pi,count,endpoint=False); self.nutrient_x=(self.width/2+np.cos(a)*self.width*.3).astype(np.float32); self.nutrient_y=(self.height/2+np.sin(a)*self.height*.35).astype(np.float32)
        else:
            self.nutrient_x=self.rng.uniform(self.width*.15,self.width*.85,count).astype(np.float32)
            self.nutrient_y=self.rng.uniform(self.height*.08,self.height*.92,count).astype(np.float32)
        self._nutrient_phase=0.0

    def _sample(self, angle):
        sx=np.mod(np.rint(self.x+np.cos(angle)*2.2).astype(int),self.width)
        sy=np.clip(np.rint(self.y+np.sin(angle)*2.2).astype(int),0,self.height-1)
        return self.trail[sy,sx]

    def _simulate_step(self,dt):
        branch=float(self.params.get("branching",.75))
        left=self._sample(self.heading-branch); ahead=self._sample(self.heading); right=self._sample(self.heading+branch)
        turn=np.where(left>right,-branch,np.where(right>left,branch,0.0)).astype(np.float32)
        ties=(np.abs(left-right)<1e-5)&(ahead<=left)
        if ties.any(): turn[ties]=self.rng.choice(np.array([-branch,branch],dtype=np.float32),int(ties.sum()))
        self.heading+=turn*.32
        field="attractor" if self.plant_modifier_strength("attractor")>0 else ("repulsor" if self.plant_modifier_strength("repulsor")>0 else "")
        if field:
            strength=self.plant_modifier_strength(field); masks=self.get_plant_masks()
            ix=np.mod(self.x.astype(int),self.width); iy=np.clip(self.y.astype(int),0,self.height-1)
            sign=-1.0 if field=="attractor" else 1.0
            self.heading += sign*strength*(masks.normal_y.T[iy,ix]*np.sin(self.heading)-masks.normal_x.T[iy,ix]*np.cos(self.heading))*.35
        speed=8.0*dt
        oldx=self.x.copy(); oldy=self.y.copy()
        self.x=np.mod(self.x+np.cos(self.heading)*speed,self.width)
        self.y=np.clip(self.y+np.sin(self.heading)*speed,0,self.height-1)
        obstacle=self.plant_modifier_strength("obstacle")
        if obstacle>0:
            masks=self.get_plant_masks(); ix=np.mod(self.x.astype(int),self.width); iy=np.clip(self.y.astype(int),0,self.height-1); hit=masks.obstacle.T[iy,ix]
            self.x[hit]=oldx[hit]; self.y[hit]=oldy[hit]; self.heading[hit]+=np.pi*.73
        ix=np.mod(self.x.astype(int),self.width); iy=np.clip(self.y.astype(int),0,self.height-1)
        np.add.at(self.trail,(iy,ix),.07)
        habitat=self.plant_modifier_strength("habitat")
        if habitat>0: self.trail += self.get_plant_masks().foliage.T.astype(np.float32)*(.003*habitat)
        emitter=self.plant_modifier_strength("emitter")
        if emitter>0 and self._logical_generation%max(5,int(30-20*emitter))==0:
            edge=np.flatnonzero(self.get_plant_masks().obstacle_edge.T)
            if edge.size:
                chosen=self.rng.choice(edge,size=min(8,edge.size),replace=False); m=min(chosen.size,self.x.size)
                ey,ex=np.unravel_index(chosen[:m],self.trail.shape); self.x[:m]=ex; self.y[:m]=ey
        d=float(self.params.get("diffusion",.62))
        blurred=(self.trail+np.roll(self.trail,1,0)+np.roll(self.trail,-1,0)+np.roll(self.trail,1,1)+np.roll(self.trail,-1,1))/5
        self.trail=(self.trail*(1-d*.18)+blurred*(d*.18))*.986
        np.clip(self.trail,0,1.5,out=self.trail)
        self._nutrient_phase+=dt*.035
        for nx,ny in zip(self.nutrient_x,self.nutrient_y):
            tx=int(np.clip(nx+np.sin(self._nutrient_phase+ny)*1.5,0,self.width-1)); ty=int(np.clip(ny+np.cos(self._nutrient_phase+nx)*2,0,self.height-1)); self.trail[ty,tx]=min(1.5,self.trail[ty,tx]+.12)

    def _render_scene(self,elapsed):
        dark,mid,light=(np.asarray(c,dtype=np.float32) for c in self._palette())
        value=np.clip(self.trail,0,1); pulse=float(self.params.get("pulse_visibility",.4))*(.5+.5*np.sin(elapsed*.8+self.trail*5))
        value=np.clip(value*(1+.18*pulse),0,1)
        canvas=dark+value[...,None]*(mid-dark)+np.clip(value-.45,0,1)[...,None]*(light-mid)
        return self._finish_canvas(np.clip(canvas,0,255).astype(np.uint8))

    def logical_state(self):
        return (self.x.tobytes(),self.y.tobytes(),self.heading.tobytes(),self.trail.tobytes(),round(self._nutrient_phase,6))

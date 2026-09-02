"""Focused Scene v2 proof for the five ambient Composer instruments."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import unittest
import numpy as np
from animation.core.manager import PreviewLEDController
from animation.plugins.gradient import GradientAnimation
from animation.plugins.rainbow import RainbowAnimation
from animation.plugins.solid import SolidColorAnimation
from animation.plugins.sparkle import SparkleAnimation
from animation.plugins.wave import WaveAnimation
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface
from web.composer_final_preview import current_component_catalog

ROOT=Path(__file__).resolve().parents[2]
FAMILIES={"gradient":(GradientAnimation,5),"rainbow":(RainbowAnimation,4),"solid":(SolidColorAnimation,5),"sparkle":(SparkleAnimation,6),"wave":(WaveAnimation,6)}

class AmbientShowcaseSceneV2Tests(unittest.TestCase):
    def setUp(self): self.interface=AnimationWebInterface(_WallChannel(),_PreviewManager(),local_mode=True); self.client=self.interface.app.test_client()
    def scene(self, component):
        value=_current_scene(); value["animation"]={"component_id":component,"version":1,"provider":"python","role":"animation","parameters":{}}; return value
    def test_exact_authored_rows_are_local_and_no_default(self):
        for component,(renderer,count) in FAMILIES.items():
            paths=sorted((ROOT/"animation"/"plugins"/component/"presets").glob("*.json")); self.assertEqual(len(paths),count); self.assertNotIn("default",[path.stem for path in paths])
            for path in paths:
                raw=json.loads(path.read_text()); self.assertEqual(raw["preset_id"],path.stem); self.assertEqual(raw["animation"],component)
                self.assertSetEqual(set(renderer._normalized_parameters(raw["params"])),set(renderer.DEFAULTS)); self.assertFalse({"brightness","palette","plant_aware","speed","color_saturation"}&set(raw["params"]))
    def test_catalog_preview_live_and_invalid_candidate_leave_state_unchanged(self):
        for sequence,(component,(renderer,_)) in enumerate(FAMILIES.items(),1):
            descriptor=current_component_catalog().require(provider="python",component_id=component,version=1); self.assertEqual(descriptor.alpha_behavior.value,"opaque")
            choice=self.interface.composer_presets.choices(component)[0]; scene=self.interface.composer_presets.apply(self.scene(component),choice["preset_id"])
            request={"origin":"composer","scene":scene,"preview":{"monotonic_elapsed":2.,"wall_time":"2026-09-01T12:00:00+00:00"}}
            self.assertEqual(self.client.post("/api/composer/preview",json=request).status_code,200)
            self.assertEqual(self.client.post("/api/composer/scene",json={"origin":"composer","scene":scene,"client_id":component,"client_sequence":sequence}).status_code,200)
            before=self.client.get("/api/composer/status").get_json(); invalid=copy.deepcopy(scene); invalid["animation"]["parameters"][next(iter(renderer.DEFAULTS))]="invalid"
            self.assertEqual(self.client.post("/api/composer/scene",json={"origin":"composer","scene":invalid,"client_id":component,"client_sequence":sequence+20}).status_code,400)
            after=self.client.get("/api/composer/status").get_json(); self.assertEqual(after["current"],before["current"])
    def test_renderers_are_bounded_deterministic_and_distinct(self):
        frames=[]
        for _,(renderer,_) in FAMILIES.items():
            left=renderer(PreviewLEDController(33,138)); right=renderer(PreviewLEDController(33,138))
            a=left.generate_frame(1.,0).pixels.copy(); b=right.generate_frame(1.,0).pixels.copy(); self.assertEqual(a.shape,(33*138,3)); self.assertEqual(a.dtype,np.uint8); self.assertTrue(np.array_equal(a,b)); frames.append(a)
        self.assertEqual(len({frame.tobytes() for frame in frames}),len(frames))
    def test_remix_keeps_hidden_local_parameters(self):
        scene=self.interface.composer_presets.apply(self.scene("wave"),"solar-radio"); remix=copy.deepcopy(scene); remix["animation"]["parameters"]["shape"]=.33
        live=self.client.post("/api/composer/scene",json={"origin":"composer","scene":remix,"client_id":"ambient-remix","client_sequence":1}).get_json()
        recovered=self.client.get("/api/composer/recovery?client_id=ambient-remix").get_json()["recovery"]["scene"]["animation"]["parameters"]
        self.assertEqual(live["state"],"live"); self.assertEqual(recovered["shape"],.33); self.assertEqual(recovered["seed"],6154)

if __name__=="__main__": unittest.main()

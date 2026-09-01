"""Focused Scene v2 proof for the remaining living-system authored packet."""

from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.core.plugin_loader import AnimationPluginLoader
from animation.plugins.living_ecosystem import LivingEcosystemAnimation
from animation.plugins.physarum_network import PhysarumNetworkAnimation
from animation.plugins.reaction_diffusion_garden import ReactionDiffusionGardenAnimation
from animation.plugins.wind_in_the_reeds import WindInTheReedsAnimation
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface
from web.composer_component_presets import ComponentPresetCatalog
from web.composer_final_preview import ComposerFinalPreview, current_component_catalog


ROOT = Path(__file__).resolve().parents[2]
RENDERERS = {"living_ecosystem": LivingEcosystemAnimation, "physarum_network": PhysarumNetworkAnimation, "reaction_diffusion_garden": ReactionDiffusionGardenAnimation, "wind_in_the_reeds": WindInTheReedsAnimation}
COUNTS = {"living_ecosystem": 9, "physarum_network": 4, "reaction_diffusion_garden": 4, "wind_in_the_reeds": 3}


class LivingShowcaseSceneV2Tests(unittest.TestCase):
    def setUp(self): self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    @staticmethod
    def scene(component_id):
        scene = _current_scene(); scene["animation"] = {"component_id":component_id,"version":1,"provider":"python","role":"animation","parameters":{}}; return scene

    def test_exact_twenty_authored_rows_are_local_and_no_default(self):
        presets = ComponentPresetCatalog(ROOT, {key: cls._normalized_parameters for key, cls in RENDERERS.items()})
        self.assertEqual(sum(COUNTS.values()), 20)
        for component_id, count in COUNTS.items():
            paths = sorted((ROOT / "animation/plugins" / component_id / "presets").glob("*.json")); self.assertEqual(len(paths), count); self.assertNotIn("default", [path.stem for path in paths])
            choices = presets.choices(component_id); self.assertEqual(len(choices), count)
            for choice in choices: self.assertSetEqual(set(choice["parameters"]), set(RENDERERS[component_id].COMPONENT_DEFAULTS)); self.assertFalse({"brightness","speed","palette"} & set(choice["parameters"]))
        animation_ids = [descriptor.component_id for descriptor in current_component_catalog().descriptors if descriptor.role.value == "animation"]
        authored_rows = sum(len(list((ROOT / "animation/plugins" / component_id / "presets").glob("*.json"))) for component_id in animation_ids)
        self.assertEqual((len(animation_ids), authored_rows), (39, 211))

    def test_normal_plugin_loader_discovers_exactly_one_concrete_class_per_living_plugin(self):
        loader = AnimationPluginLoader(allowed_plugins=set(RENDERERS))
        loaded = loader.load_all_plugins()
        self.assertEqual(set(loaded), set(RENDERERS))
        for component_id, renderer in RENDERERS.items():
            self.assertEqual(loaded[component_id].__name__, renderer.__name__)
            self.assertEqual(loaded[component_id](self.controller).generate_frame(0., 0).pixels.shape, (33 * 138, 3))

    def test_qualified_catalog_final_preview_and_bounded_cadence(self):
        catalog = current_component_catalog(); fingerprints = set()
        for component_id, renderer in RENDERERS.items():
            descriptor = renderer.component_descriptor(); self.assertIs(catalog.require(provider="python", component_id=component_id, version=1), descriptor); self.assertEqual((descriptor.alpha_behavior.value, descriptor.palette_policy.value), ("opaque", "semantic"))
            animation = renderer(self.controller, renderer.COMPONENT_DEFAULTS); first = animation.generate_frame(0., 0); later = animation.generate_frame(1.2, 1)
            self.assertEqual(first.pixels.shape, (33 * 138, 3)); self.assertEqual(first.pixels.dtype, np.uint8); self.assertFalse(np.array_equal(first.pixels, later.pixels)); fingerprints.add(first.pixels.tobytes())
            frame = ComposerFinalPreview(catalog, ROOT).render(__import__('ipc.scene_contract', fromlist=['normalize_composer_scene']).normalize_composer_scene({"origin":"composer","scene":self.scene(component_id)}, catalog), 1., datetime.now().astimezone()); self.assertEqual(frame.pixels.shape, (33 * 138, 3))
        self.assertEqual(len(fingerprints), len(RENDERERS))

    def test_remix_preserves_hidden_parameters_and_invalid_candidates_rollback(self):
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True); client = interface.app.test_client()
        for index, (component_id, renderer) in enumerate(RENDERERS.items(), 1):
            selected = interface.composer_presets.apply(self.scene(component_id), "showcase" if component_id != "living_ecosystem" else "temperate-wetland")
            remix = copy.deepcopy(selected); key = {"living_ecosystem":"migration", "physarum_network":"branching", "reaction_diffusion_garden":"growth_rate", "wind_in_the_reeds":"wind"}[component_id]; remix["animation"]["parameters"][key] = .45
            live = client.post("/api/composer/scene", json={"origin":"composer","scene":remix,"client_id":component_id,"client_sequence":index}); self.assertEqual(live.status_code, 200)
            before = client.get(f"/api/composer/recovery?client_id={component_id}").get_json()
            invalid = copy.deepcopy(remix); invalid["animation"]["parameters"][key] = 99999
            self.assertEqual(client.post("/api/composer/preview", json={"origin":"composer","scene":invalid,"preview":{"monotonic_elapsed":1.,"wall_time":"2026-09-01T12:00:00+00:00"}}).status_code, 400)
            self.assertEqual(client.post("/api/composer/scene", json={"origin":"composer","scene":invalid,"client_id":component_id,"client_sequence":index+20}).status_code, 400)
            self.assertEqual(client.get(f"/api/composer/recovery?client_id={component_id}").get_json(), before)


if __name__ == "__main__": unittest.main()

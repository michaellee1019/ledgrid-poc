"""Focused Scene-v2 proof for the five authored light sculptures."""

from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.cellular_tapestry import CellularTapestryAnimation
from animation.plugins.flow_field_silk import FlowFieldSilkAnimation
from animation.plugins.frostwork import FrostworkAnimation
from animation.plugins.living_stained_glass import LivingStainedGlassAnimation
from animation.plugins.quasicrystal_bloom import QuasicrystalBloomAnimation
from ipc.scene_contract import canonical_json_bytes, normalize_composer_scene
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface
from web.composer_component_presets import ComponentPresetCatalog
from web.composer_final_preview import ComposerFinalPreview, current_component_catalog


ROOT = Path(__file__).resolve().parents[2]
RENDERERS = {
    "cellular_tapestry": CellularTapestryAnimation, "flow_field_silk": FlowFieldSilkAnimation,
    "frostwork": FrostworkAnimation, "living_stained_glass": LivingStainedGlassAnimation,
    "quasicrystal_bloom": QuasicrystalBloomAnimation,
}
COUNTS = {"cellular_tapestry": 3, "flow_field_silk": 3, "frostwork": 4,
          "living_stained_glass": 8, "quasicrystal_bloom": 4}


class SculptureShowcaseSceneV2Tests(unittest.TestCase):
    def setUp(self): self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    @staticmethod
    def scene(component_id):
        scene = _current_scene()
        scene["animation"] = {"component_id": component_id, "version": 1, "provider": "python", "role": "animation", "parameters": {}}
        return scene

    def test_exact_authored_rows_are_qualified_choices_without_default(self):
        presets = ComponentPresetCatalog(ROOT, {key: cls._normalized_parameters for key, cls in RENDERERS.items()})
        self.assertEqual(sum(COUNTS.values()), 22)
        for component_id, count in COUNTS.items():
            paths = sorted((ROOT / "animation/plugins" / component_id / "presets").glob("*.json"))
            self.assertEqual(len(paths), count); self.assertNotIn("default", [path.stem for path in paths])
            choices = presets.choices(component_id); self.assertEqual(len(choices), count)
            for path, choice in zip(paths, choices):
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(path.stem, choice["preset_id"])
                self.assertSetEqual(set(choice["parameters"]), set(RENDERERS[component_id].COMPONENT_DEFAULTS))
                self.assertNotIn("brightness", choice["parameters"]); self.assertNotIn("speed", choice["parameters"])

    def test_catalog_preview_distinct_bounded_and_semantic(self):
        catalog = current_component_catalog(); fingerprints = set()
        for component_id, renderer in RENDERERS.items():
            descriptor = renderer.component_descriptor()
            self.assertIs(catalog.require(provider="python", component_id=component_id, version=1), descriptor)
            self.assertEqual((descriptor.alpha_behavior.value, descriptor.palette_policy.value), ("opaque", "semantic"))
            animation = renderer(self.controller, renderer.COMPONENT_DEFAULTS)
            first, next_frame = animation.generate_frame(0., 0), animation.generate_frame(1.2, 1)
            self.assertEqual(first.pixels.shape, (33 * 138, 3)); self.assertEqual(first.pixels.dtype, np.uint8)
            self.assertFalse(np.array_equal(first.pixels, next_frame.pixels)); fingerprints.add(first.pixels.tobytes())
            canonical = normalize_composer_scene({"origin":"composer", "scene":self.scene(component_id)}, catalog)
            frame = ComposerFinalPreview(catalog, ROOT).render(canonical, 1., datetime.now().astimezone())
            self.assertEqual(frame.pixels.shape, (33 * 138, 3))
        self.assertEqual(len(fingerprints), len(RENDERERS))

    def test_api_normalizes_browser_numbers_and_rejects_before_mutation(self):
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True); client = interface.app.test_client()
        for component_id, count in COUNTS.items(): self.assertEqual(len(client.get(f"/api/composer/components/{component_id}/presets").get_json()["presets"]), count)
        authored = interface.composer_presets.apply(self.scene("living_stained_glass"), "showcase")
        authored["animation"]["parameters"]["light_direction"] = 0.0
        browser = copy.deepcopy(authored); browser["animation"]["parameters"]["light_direction"] = 0
        canonical = interface._composer_canonical({"origin":"composer", "scene":authored})
        self.assertEqual(canonical.canonical_bytes, interface._composer_canonical({"origin":"composer", "scene":browser}).canonical_bytes)
        self.assertEqual(canonical.canonical_bytes, canonical_json_bytes(canonical.scene))
        accepted = client.post("/api/composer/scene", json={"origin":"composer", "scene":browser, "client_id":"sculpture", "client_sequence":1})
        self.assertEqual(accepted.status_code, 200); body = accepted.get_json(); self.assertEqual(body["current"], body["desired"]); self.assertEqual(body["desired"], body["observed"])
        baseline = client.get("/api/composer/recovery?client_id=sculpture").get_json()
        invalid = copy.deepcopy(browser); invalid["animation"]["parameters"]["lead_width"] = 4
        self.assertEqual(client.post("/api/composer/preview", json={"origin":"composer", "scene":invalid, "preview":{"monotonic_elapsed":1., "wall_time":"2026-09-01T12:00:00+00:00"}}).status_code, 400)
        self.assertEqual(client.post("/api/composer/scene", json={"origin":"composer", "scene":invalid, "client_id":"sculpture", "client_sequence":2}).status_code, 400)
        self.assertEqual(client.get("/api/composer/recovery?client_id=sculpture").get_json(), baseline)

    def test_selected_controls_preserve_hidden_parameters_and_render_only_selected_group(self):
        script = (ROOT / "web/static/js/composer_slice.js").read_text(encoding="utf-8")
        for token in ("const sculptureIds", "sculptureParameters = (id, existing = {}) => ({...existing", "Object.values(sculptureControls).flat().forEach", "...Object.entries(sculpturePresetTargets)", "installSculptureControls()"):
            self.assertIn(token, script)


if __name__ == "__main__": unittest.main()

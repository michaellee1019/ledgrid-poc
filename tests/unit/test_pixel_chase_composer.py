"""Execute the shipped Pixel Chase control-to-Scene wiring."""
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]


class PixelChaseComposerTests(unittest.TestCase):
    def test_current_presets_reach_exact_guarded_controller_receipts(self):
        from copy import deepcopy
        from animation.core.activation_qualification import canonical_json_sha256
        from tests.unit.test_canonical_scene_activation import CanonicalSceneActivationTests

        CanonicalSceneActivationTests.setUpClass()
        fixture = CanonicalSceneActivationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        response = fixture.client.get('/api/composer/components/pixel_chase/presets')
        self.assertEqual(response.status_code, 200, response.get_json())
        presets = response.get_json()['presets']
        self.assertEqual({preset['preset_id'] for preset in presets}, {
            'steady-lanterns', 'prismatic-relay', 'comet-trails',
        })
        for preset in presets:
            with self.subTest(preset=preset['preset_id']):
                scene = deepcopy(fixture.scene)
                scene['animation'] = {
                    'component_id': 'pixel_chase', 'version': 1, 'provider': 'python',
                    'role': 'animation', 'parameters': preset['parameters'],
                }
                canonical = fixture.interface._composer_canonical({'origin': 'composer', 'scene': scene}).scene
                _, _, receipt = fixture.activate(canonical)
                self.assertEqual(receipt['phase'], 'active', receipt)
                self.assertEqual(fixture.manager.get_scene_state(), canonical)
                self.assertEqual(receipt['observed_identity']['scene_identity']['digest'], canonical_json_sha256(canonical))

    def test_selection_and_remix_preserve_complete_scene(self):
        from web.composer_final_preview import current_component_catalog
        from ipc.scene_contract import normalize_composer_scene
        from web.starter_looks import get_starter

        catalog = current_component_catalog()
        descriptor = catalog.require(provider="python", component_id="pixel_chase", version=1)
        scene = get_starter("aurora")["scene"]
        scene["animation"] = {"component_id": "pixel_chase", "version": 1, "provider": "python",
                              "role": "animation", "parameters": {'pixels_per_second': 250}}
        canonical = normalize_composer_scene({"origin": "composer", "scene": scene}, catalog).scene
        self.assertEqual(canonical["animation"]["parameters"], descriptor.default_parameters() | {'pixels_per_second': 250})
        for section in ("background", "widgets", "plants", "look"):
            self.assertEqual(canonical[section], scene[section])

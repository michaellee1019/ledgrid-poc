"""Execute the shipped Plant Glow control-to-Scene wiring."""

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]


class PlantGlowComposerTests(unittest.TestCase):
    def test_current_presets_reach_exact_guarded_controller_receipts(self):
        from copy import deepcopy

        from animation.core.activation_qualification import canonical_json_sha256
        from tests.unit.test_canonical_scene_activation import (
            CanonicalSceneActivationTests,
        )

        CanonicalSceneActivationTests.setUpClass()
        fixture = CanonicalSceneActivationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        response = fixture.client.get("/api/composer/components/plant_glow/presets")
        self.assertEqual(response.status_code, 200, response.get_json())
        presets = response.get_json()["presets"]
        self.assertEqual(
            {preset["preset_id"] for preset in presets},
            {"canopy-pulse", "fine-veins", "globe-constellation", "soft-moss"},
        )
        for preset in presets:
            with self.subTest(preset=preset["preset_id"]):
                scene = deepcopy(fixture.scene)
                scene["animation"] = {
                    "component_id": "plant_glow",
                    "version": 1,
                    "provider": "python",
                    "role": "animation",
                    "parameters": preset["parameters"],
                }
                canonical = fixture.interface._composer_canonical(
                    {"origin": "composer", "scene": scene}
                ).scene
                _, _, receipt = fixture.activate(canonical)
                self.assertEqual(receipt["phase"], "active", receipt)
                self.assertEqual(fixture.manager.get_scene_state(), canonical)
                self.assertEqual(
                    receipt["observed_identity"]["scene_identity"]["digest"],
                    canonical_json_sha256(canonical),
                )

    def test_shipped_node_selection_and_remix_preserve_complete_scene(self):
        source = (ROOT / "web/static/js/composer_slice.js").read_text()
        fields = source[
            source.index("const plantGlowFields"):
            source.index("const componentPresetTargets")
        ]
        factory = source[
            source.index("function sceneFromControls()"):
            source.index("function applyScene(")
        ]
        script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const values = {
  backgroundGain: '.62', animationChoice: 'plant_glow', previewPalette: 'spectrum',
  sceneSpeed: '.75', sceneLuminance: '1.25', plantGlowRadius: '4',
  plantGlowStrength: '1.2', plantGlowFalloff: '.8', plantGlowBreathSpeed: '.44',
  plantGlowBreathDepth: '.36', plantGlowShimmer: '.22', plantGlowFoliage: '.76',
  plantGlowGlobes: '1.3',
};
const nodes = Object.fromEntries(Object.entries(values).map(([id, value]) => [id, {value}]));
const prior = {
  schema: 'ledgrid.scene.v2',
  background: {component_id: 'native_aurora', version: 1, provider: 'receiver_native', role: 'background', bundle_digest: 'a'.repeat(64), parameters: {gain: .62, source_fps: 30, seed: 8012}},
  animation: {component_id: 'pixel_chase', parameters: {pixels_per_second: 120}},
  widgets: [{id: 'retained.message', visible: false, component: {component_id: 'emoji_arranger', parameters: {text: 'Hello'}}, placement: {mode: 'auto'}}],
  plants: {effects: {version: 1, active: ['shadow'], strengths: {shadow: .35}}},
  look: {palette_id: 'mist', pace: 1, presentation_brightness: 1},
};
const state = {scene: structuredClone(prior), lastControl: 'animationChoice'};
const context = vm.createContext({
  state, structuredClone, document: {getElementById: (id) => nodes[id]},
  $: (selector) => nodes[selector.slice(1)],
  number: (selector) => Number(nodes[selector.slice(1)].value),
  sculptureIds: [], atmosphereIds: [], ambientIds: [], applyPlantOptics: () => {},
});
vm.runInContext(process.argv[1] + process.argv[2], context);
const selected = JSON.parse(JSON.stringify(vm.runInContext('sceneFromControls()', context)));
assert.deepEqual(selected.animation, {component_id: 'plant_glow', version: 1, provider: 'python', role: 'animation', parameters: {
  glow_radius: 4, glow_strength: 1.2, glow_falloff: .8, breath_speed: .44,
  breath_depth: .36, shimmer: .22, foliage_intensity: .76, globe_intensity: 1.3,
}});
assert.deepEqual(selected.background, prior.background);
assert.deepEqual(selected.widgets, prior.widgets);
assert.deepEqual(selected.plants, prior.plants);
assert.deepEqual(selected.look, {palette_id: 'spectrum', pace: .75, presentation_brightness: 1.25});
assert.deepEqual(state.scene, prior);
state.scene = selected; state.lastControl = 'plantGlowStrength';
nodes.plantGlowStrength.value = '.31'; nodes.plantGlowRadius.value = '1';
nodes.plantGlowGlobes.value = '.42';
const remixed = JSON.parse(JSON.stringify(vm.runInContext('sceneFromControls()', context)));
assert.equal(remixed.animation.component_id, 'plant_glow');
assert.equal(remixed.animation.parameters.glow_strength, .31);
assert.equal(remixed.animation.parameters.glow_radius, 1);
assert.equal(remixed.animation.parameters.globe_intensity, .42);
assert.deepEqual(remixed.background, prior.background);
assert.deepEqual(remixed.widgets, prior.widgets);
assert.deepEqual(remixed.plants, prior.plants);
assert.deepEqual(selected.animation.parameters, {
  glow_radius: 4, glow_strength: 1.2, glow_falloff: .8, breath_speed: .44,
  breath_depth: .36, shimmer: .22, foliage_intensity: .76, globe_intensity: 1.3,
});
"""
        result = subprocess.run(
            ["node", "-e", script, fields, factory], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

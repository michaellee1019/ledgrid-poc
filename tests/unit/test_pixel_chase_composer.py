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
        source = (ROOT / 'web/static/js/composer_slice.js').read_text()
        fields = source[source.index('const pixelChaseFields'):source.index('const componentPresetTargets')]
        factory = source[source.index('function sceneFromControls()'):source.index('function applyScene(')]
        script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const values = {
  backgroundGain: '.62', animationChoice: 'pixel_chase', previewPalette: 'ember',
  sceneSpeed: '.75', sceneLuminance: '1.25', chaseRate: '250', chaseCount: '7',
  chaseTailStyle: 'solid', chaseTailLength: '12', chaseCycle: '.4',
};
const nodes = Object.fromEntries(Object.entries(values).map(([id, value]) => [id, {value}]));
const prior = {
  schema: 'ledgrid.scene.v2',
  background: {component_id: 'native_aurora', version: 1, provider: 'receiver_native', role: 'background', bundle_digest: 'a'.repeat(64), parameters: {gain: .62, source_fps: 30, seed: 8012}},
  animation: {component_id: 'aurora_curtains', parameters: {seed: 4201}},
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
assert.deepEqual(selected.animation, {component_id: 'pixel_chase', version: 1, provider: 'python', role: 'animation', parameters: {
  pixels_per_second: 250, pixel_count: 7, tail_style: 'solid', tail_length: 12, color_cycle_speed: .4,
}});
assert.deepEqual(selected.background, prior.background);
assert.deepEqual(selected.widgets, prior.widgets);
assert.deepEqual(selected.plants, prior.plants);
assert.deepEqual(selected.look, {palette_id: 'ember', pace: .75, presentation_brightness: 1.25});
assert.deepEqual(state.scene, prior);
state.scene = selected; state.lastControl = 'chaseRate';
nodes.chaseRate.value = '22'; nodes.chaseTailStyle.value = 'none'; nodes.chaseCycle.value = '0';
const remixed = JSON.parse(JSON.stringify(vm.runInContext('sceneFromControls()', context)));
assert.equal(remixed.animation.component_id, 'pixel_chase');
assert.equal(remixed.animation.parameters.pixels_per_second, 22);
assert.equal(remixed.animation.parameters.tail_style, 'none');
assert.equal(remixed.animation.parameters.color_cycle_speed, 0);
assert.deepEqual(remixed.widgets, prior.widgets);
assert.deepEqual(selected.animation.parameters, {pixels_per_second: 250, pixel_count: 7, tail_style: 'solid', tail_length: 12, color_cycle_speed: .4});
"""
        result = subprocess.run(['node', '-e', script, fields, factory], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

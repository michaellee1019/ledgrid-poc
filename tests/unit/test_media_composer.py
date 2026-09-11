"""Current media selection, local controls and exact Composer activation."""
from copy import deepcopy
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


class MediaComposerTests(unittest.TestCase):
    def test_all_retained_media_presets_reach_exact_controller_receipts(self):
        from tests.unit.test_canonical_scene_activation import CanonicalSceneActivationTests
        CanonicalSceneActivationTests.setUpClass()
        fixture = CanonicalSceneActivationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for component, count in (("gif_animation", 32), ("world_flags", 8)):
            response = fixture.client.get(f"/api/composer/components/{component}/presets")
            self.assertEqual(response.status_code, 200, response.get_json())
            presets = response.get_json()["presets"]
            self.assertEqual(len(presets), count)
            for preset in presets:
                with self.subTest(component=component, preset=preset["preset_id"]):
                    scene = deepcopy(fixture.scene)
                    scene["animation"] = dict(component_id=component, version=1,
                        provider="python", role="animation", parameters=preset["parameters"])
                    canonical = fixture.interface._composer_canonical({"origin":"composer", "scene":scene}).scene
                    _, _, receipt = fixture.activate(canonical)
                    self.assertEqual(receipt["phase"], "active", receipt)
                    self.assertEqual(receipt["requested_identity"], receipt["observed_identity"])
                    self.assertEqual(fixture.manager.get_scene_state(), canonical)

    def test_shipped_media_selection_and_control_remix_preserve_scene(self):
        source = (ROOT / "web/static/js/composer_slice.js").read_text()
        fields = source[source.index("const mediaSpecs"):source.index("const componentPresetTargets")]
        factory = source[source.index("function sceneFromControls()"):source.index("function applyScene(")]
        script = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const nodes = {
  animationChoice:{value:'gif_animation'}, backgroundGain:{value:'.62'},
  previewPalette:{value:'ember'}, sceneSpeed:{value:'.7'}, sceneLuminance:{value:'.8'},
};
const prior = {schema:'ledgrid.scene.v2',
  background:{component_id:'native_aurora',parameters:{gain:.62,seed:8012}},
  animation:{component_id:'plant_glow',parameters:{glow_strength:.72}},
  widgets:[{id:'keep-widget',visible:true}],
  plants:{effects:{version:1,active:['shadow'],strengths:{shadow:.35}}},
  look:{palette_id:'mist',pace:1,presentation_brightness:1},
};
const state = {scene:structuredClone(prior),lastControl:'animationChoice'};
const context = vm.createContext({state,structuredClone,
 document:{getElementById:id=>nodes[id]}, $:selector=>nodes[selector.slice(1)],
 number:selector=>Number(nodes[selector.slice(1)].value),applyPlantOptics:()=>{},
 sculptureIds:[],atmosphereIds:[],ambientIds:[],
});
vm.runInContext(process.argv[1]+process.argv[2],context);
const specs = JSON.parse(JSON.stringify(vm.runInContext('mediaSpecs',context)));
for (const [id,spec] of Object.entries(specs)) {
  for (const field of spec.fields) nodes[`${spec.prefix}_${field.key}`] = {value:String(field.value),checked:field.value};
}
const render=()=>JSON.parse(JSON.stringify(vm.runInContext('sceneFromControls()',context)));
for (const [id, spec] of Object.entries(specs)) {
  state.scene=structuredClone(prior);nodes.animationChoice.value=id;
  const selected=render();
  assert.equal(selected.animation.component_id,id);
  assert.equal(selected.animation.role,'animation');
  assert.deepEqual(selected.animation.parameters,Object.fromEntries(spec.fields.map(f=>[f.key,f.value])));
  for(const key of ['background','widgets','plants']) assert.deepEqual(selected[key],prior[key]);
  assert.deepEqual(state.scene,prior);
  state.scene=selected;
  const numberField=spec.fields.find(f=>!f.options&&f.type!=='checkbox');
  const checkbox=spec.fields.find(f=>f.type==='checkbox');
  nodes[`${spec.prefix}_${numberField.key}`].value='2';
  nodes[`${spec.prefix}_${checkbox.key}`].checked=!checkbox.value;
  state.lastControl=`${spec.prefix}_${numberField.key}`;
  const remixed=render();
  assert.equal(remixed.animation.parameters[numberField.key],2);
  assert.equal(remixed.animation.parameters[checkbox.key],!checkbox.value);
  for(const key of ['background','widgets','plants']) assert.deepEqual(remixed[key],prior[key]);
}
'''
        result = subprocess.run(["node", "-e", script, fields, factory], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

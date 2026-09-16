"""Slow startup recovery must not replace a newer operator edit."""
from pathlib import Path
import subprocess
import unittest


class ComposerStartupIntentTests(unittest.TestCase):
    def test_recovery_preserves_edits_and_still_hydrates_an_idle_client(self):
        script = Path('web/static/js/composer_slice.js').read_text()
        source = script[script.index('async function hydrateCurrentScene()'):script.index('  renderPlaylist();', script.index('async function hydrateCurrentScene()'))]
        javascript = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
async function run({editAt, ok=true, current=false}) {
  const state = {intent:0, scene:null, dirty:false};
  let applied=[], submitted=[], recovered=0;
  const newer = {animation:{component_id:'aurora_curtains'}};
  const old = {animation:{component_id:'cellular_tapestry'}};
  function edit() { state.intent++; state.scene=newer; state.dirty=true; }
  const context = {
    state, api:'/api/composer', clientId:'test', encodeURIComponent,
    intentIsCurrent:intent=>intent===state.intent,
    refreshStatus:async()=>{if(editAt==='status') edit();},
    fetch:async()=>({ok,json:async()=>{
      if(editAt==='recovery') edit();
      return {recovery:{scene:old},status:{current},error:'Old recovery failed'};
    }}),
    applyScene:scene=>applied.push(scene),
    submit:async scene=>submitted.push(scene),
    recoverFromInvalidRecovery:()=>recovered++,
    defaultScene:()=>old,
  };
  vm.runInNewContext(process.argv[1],context);
  await context.hydrateCurrentScene();
  if(editAt) {
    assert.equal(state.scene,newer,'startup replaced newer operator scene');
    assert.equal(state.dirty,true);
    assert.deepEqual(applied,[]); assert.deepEqual(submitted,[]);
    assert.equal(recovered,0);
  } else {
    assert.equal(state.scene,old); assert.deepEqual(applied,[old]);
    assert.equal(submitted.length,current?0:1);
  }
}
(async()=>{
  await run({editAt:'status'});
  await run({editAt:'recovery'});
  await run({editAt:'recovery',ok:false});
  await run({});
  await run({current:true});
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(['node', '-e', javascript, source], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        old = source.replace('    if (!intentIsCurrent(hydrationIntent)) return;\n', '')
        negative = subprocess.run(['node', '-e', javascript, old], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(negative.returncode, 0)
        self.assertIn('startup replaced newer operator scene', negative.stderr)

"""Playback labels must follow observations rather than requested selections."""
from pathlib import Path
import subprocess
import unittest


class PlaybackPresentationTests(unittest.TestCase):
    def test_pending_rejected_offline_and_stopped_labels(self):
        script = Path('web/static/js/composer_slice.js').read_text()
        source = script[script.index('function observedWallIdentity'):script.index('  function composerSceneFromWall')] + script[script.index('function renderStatus'):script.index('  function updateHistoryActions')]
        runner = r'''
const assert = require('node:assert/strict'), vm = require('node:vm');
const nodes = new Map();
const $ = id => {if(!nodes.has(id))nodes.set(id,{textContent:'',replaceChildren(){},append(){},addEventListener(){}});return nodes.get(id);};
const scene = id => ({animation:{component_id:id}});
const observation = {controller_session_id:'session',freshness:'fresh',is_running:true,scene:scene('aurora_curtains'),scene_digest:null,receiver_connectivity:{connected:[0,1,2,3]}};
const state = {scene:scene('fireworks'),wall:{observation,requested:scene('tetris'),scene:scene('fireworks')},publication:{},gallery:{entries:[{component_id:'aurora_curtains',name:'Aurora Curtains'},{component_id:'fireworks',name:'Fireworks'},{component_id:'tetris',name:'Tetris'}]}};
const context = {state,$,identity:x=>x?.digest || 'None'};
vm.runInNewContext(process.argv[1]+';this.render=renderStatus;',context);
const render = () => context.render({connected:true,running:true,current:{digest:'requested'}});
render();
assert.equal($('#operations-title').textContent,'Playing · Aurora Curtains','requested wall.scene cannot supply observed name');
assert.ok(state.status.current,'fresh controller scene without digest remains usable for interactions');
assert.match(state.status.current.digest,/digest unavailable/);
observation.scene=null;render();assert.equal(state.status.current,null,'requested scene cannot substitute for missing observed scene');
observation.scene=scene('aurora_curtains');render();
assert.equal($('#workingSceneContext').textContent,'Editing: Fireworks · local preview');
assert.equal($('#requestedSceneSummary').textContent,'Last scene requested: Tetris');
assert.equal($('#receiverSummary').textContent,'Receivers: 4/5 connected');
assert.equal($('#liveAction').textContent,'Stop output');assert.equal($('#liveAction').disabled,false);
state.publication.inFlight={kind:'publish',scene:scene('fireworks')};render();
assert.equal($('#operations-title').textContent,'Playing · Aurora Curtains');assert.match($('#playbackHint').textContent,/pending/);assert.match($('#requestedSceneSummary').textContent,/Requesting: Fireworks/);
state.publication.inFlight=null;state.wall.activationError='Rejected';render();render();
assert.equal($('#wallActivationFailure').hidden,false);assert.equal($('#wallActionMessage').textContent,'Rejected');
state.wall.actionKind='stop';render();assert.equal($('#retryWallAction').textContent,'Retry stop');
for(const condition of ['stale','offline','missing']) {
  observation.freshness=condition==='stale'?'stale':'fresh';state.wall.statusUnavailable=condition==='offline';state.wall.observation=condition==='missing'?null:observation;render();
  assert.equal($('#operations-title').textContent,'Playback unknown');assert.equal($('#liveAction').textContent,'Stop output');assert.equal($('#liveAction').disabled,true);assert.equal($('#resumeScene').hidden,true);assert.equal($('#observedIdentity').textContent,'Unknown');
}
state.wall.observation=observation;state.wall.statusUnavailable=false;observation.freshness='fresh';observation.is_running=false;state.wall.activationError=null;render();
assert.equal($('#operations-title').textContent,'Output stopped');assert.equal($('#resumeScene').hidden,false);assert.equal($('#resumeScene').disabled,false);assert.equal($('#liveAction').disabled,true);
state.publication.queued={kind:'publish',scene:state.scene};render();assert.equal($('#resumeScene').disabled,true);
'''
        result = subprocess.run(['node', '-e', runner, source],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

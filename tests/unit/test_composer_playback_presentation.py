"""Playback labels must follow observations rather than requested selections."""
from pathlib import Path
import subprocess
import unittest


class PlaybackPresentationTests(unittest.TestCase):
    def test_pending_rejected_offline_and_stopped_labels(self):
        script = Path('web/static/js/composer_slice.js').read_text()
        source = script[script.index('function renderStatus'):script.index('  function updateHistoryActions')]
        runner = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const nodes = new Map();
const node = () => ({textContent:'',children:[],replaceChildren(){this.children=[];},append(child){this.children.push(child);},addEventListener(){}});
const $ = id => {if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);};
const prior = {digest:'prior',revision:1};
const state = {wall:{scene:{},observation:{active_identity:{scene_identity:prior,component_identities:[{slot_id:'animation',component_id:'aurora_curtains'}]}},activating:false},
 publication:{},gallery:{entries:[{component_id:'aurora_curtains',name:'Aurora Curtains'}]},selection:{name:'Requested Fireworks'},dirty:false};
const wallStatus = () => ({connected:true,running:true,armed:true,observed:prior,current:prior});
const context = {state,$,wallStatus,identity:x=>x?.digest || 'None',document:{createElement:node,createTextNode:text=>({textContent:text})},retryWallActivation(){}};
vm.runInNewContext(process.argv[1]+';this.render=renderStatus;',context);
const render = () => context.render({connected:true,running:true,armed:true,current:{digest:'requested'}});
render();
assert.equal($('#operations-title').textContent,'Playing · Aurora Curtains');
assert.equal($('#connectionState').textContent,'Live');
state.publication.inFlight = {scene:'Fireworks'};
render();
assert.equal($('#operations-title').textContent,'Playing · Aurora Curtains');
assert.match($('#playbackHint').textContent,/last confirmed output/);
assert.equal($('#connectionState').textContent,'Updating…');
state.publication.inFlight = null;
state.wall.activationError = 'Rejected';
render();
assert.equal($('#operations-title').textContent,'Playing · Aurora Curtains');
assert.match($('#playbackHint').textContent,/not confirmed/);
assert.equal($('#wallActivationFailure').hidden,false);
state.wall.statusUnavailable = true;
render();
assert.equal($('#operations-title').textContent,'Wall unavailable');
assert.equal($('#connectionState').textContent,'Offline');
assert.match($('#playbackHint').textContent,/Cannot confirm/);
state.wall.statusUnavailable = false;
state.wall.activationError = null;
context.wallStatus = () => ({connected:true,running:false,armed:false});
render();
assert.equal($('#operations-title').textContent,'Output stopped');
assert.match($('#playbackHint').textContent,/adjust a control to resume/);
assert.equal($('#liveAction').disabled,true);
'''
        result = subprocess.run(['node', '-e', runner, source],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

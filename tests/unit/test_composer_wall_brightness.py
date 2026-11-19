"""Composer must be able to recover a dark master output without editing a scene."""
from pathlib import Path
import subprocess
import unittest


SOURCE = Path('web/static/js/composer_slice.js')
MODULE = SOURCE.read_text().split("\n(() => {\n  'use strict';\n  const root", 1)[0]
HARNESS = r'''
const assert = require('node:assert/strict'), vm = require('node:vm');
function environment() {
  const nodes = Object.fromEntries(['wallBrightness', 'wallBrightnessValue', 'wallBrightnessStatus'].map(id =>
    [id, {value:'0', disabled:true, textContent:'', handlers:{}, addEventListener(type, callback) { this.handlers[type]=callback; }}]));
  const calls=[], responses=[];
  let signal;
  const context = {
    document:{querySelector:selector=>selector === '.workspace-header' ? {insertAdjacentHTML(){}} : nodes[selector.slice(1)]}, Date,
    AbortSignal:{timeout:()=>signal={aborted:false}},
    setTimeout:resolve=>{signal.aborted=true; resolve();},
    fetch:async(url, options)=>{
      calls.push({url, ...options});
      const item=responses.shift();
      if(item instanceof Error) throw item;
      if(!item) throw new Error('Unexpected request: '+url);
      return {ok:!item.error, json:async()=>item};
    },
  };
  vm.runInNewContext(process.argv[1], context);
  const fresh = (brightness=0, extra={}) => ({brightness, freshness:'fresh', controller_session_id:'session', controller_state_revision:7, ...extra});
  const api=context.ComposerWallBrightness, control=nodes.wallBrightness;
  const render=(item=fresh(), blocked=false)=>api.render(item, blocked);
  const change=async(brightness)=>{control.value=String(brightness); control.handlers.change(); await api.settled;};
  return {nodes, calls, responses, api, control, fresh, render, change};
}
'''


class WallBrightnessTests(unittest.TestCase):
    def run_js(self, scenario):
        result = subprocess.run(
            ['node', '-e', HARNESS + '\n(async()=>{' + scenario
             + '\n})().catch(error=>{console.error(error);process.exitCode=1});', MODULE],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_master_control_is_visible_outside_scene_editor(self):
        self.assertIn("querySelector('.workspace-header').insertAdjacentHTML", MODULE)
        self.assertIn('for="wallBrightness">Wall brightness', MODULE)
        self.assertIn('id="wallBrightness" type="range" min="0" max="255"', MODULE)

    def test_load_observes_zero_without_writing_and_preserves_active_drag(self):
        self.run_js(r'''
const e=environment(); e.render();
assert.equal(e.control.disabled,false); assert.match(e.nodes.wallBrightnessValue.textContent,/0%/);
assert.match(e.nodes.wallBrightnessStatus.textContent,/dark/); assert.equal(e.calls.length,0);
e.control.value='128'; e.control.handlers.input(); e.render(e.fresh(0));
assert.equal(e.control.value,'128'); assert.match(e.nodes.wallBrightnessValue.textContent,/50%/);
assert.equal(e.calls.length,0); e.control.handlers.blur(); assert.equal(e.control.value,'0');
e.render(e.fresh(128)); assert.equal(e.control.value,'128');
e.render(e.fresh(128,{freshness:'stale'})); assert.equal(e.control.disabled,true);
e.render(null); assert.equal(e.nodes.wallBrightnessValue.textContent,'Unknown');
''')

    def test_nonzero_and_zero_require_guarded_applied_acknowledgement(self):
        self.run_js(r'''
for (const brightness of [128,0,255]) {
  const e=environment(); e.render(e.fresh(26));
  e.responses.push(e.fresh(26), {success:true,command_id:123}, e.fresh(brightness,{controller_state_revision:8,last_applied_command_id:123}));
  await e.change(brightness);
  assert.equal(e.control.value,String(brightness)); assert.equal(e.control.disabled,false);
  assert.equal(e.nodes.wallBrightnessStatus.textContent,'Brightness applied.');
  const posts=e.calls.filter(call=>call.method==='POST'); assert.equal(posts.length,1);
  assert.equal(posts[0].url,'/api/config/brightness');
  const body=JSON.parse(posts[0].body);
  assert.equal(body.brightness,brightness); assert.equal(body.expected_controller_session_id,'session');
  assert.equal(body.expected_controller_state_revision,7); assert.ok(body.expires_at>Date.now()/1000);
  assert.equal(body.scene,undefined); assert.equal(e.calls.some(call=>call.url==='/api/v1/scene'),false);
}
''')

    def test_rejected_stale_offline_and_unconfirmed_requests_remain_failures(self):
        self.run_js(r'''
const cases = [
  [e=>[e.fresh(0,{freshness:'stale'})], /not changed/, 0],
  [e=>[new Error('Network unavailable')], /Network unavailable/, 0],
  [e=>[e.fresh(),{error:'Rejected revision'}], /Rejected revision/, 1],
  [e=>[e.fresh(),{command_id:123},e.fresh(128,{controller_session_id:'new-session'})], /restarted/, 1],
  [e=>[e.fresh(),{command_id:123},e.fresh(0,{command_result:{command_id:123,state:'failed',error:'SPI unavailable'}})], /SPI unavailable/, 1],
  [e=>[e.fresh(),{command_id:123},e.fresh(128,{controller_state_revision:8,last_applied_command_id:122})], /not confirmed/, 1],
];
for (const [responses, expected, postCount] of cases) {
  const e=environment();e.render();e.responses.push(...responses(e));await e.change(128);
  assert.match(e.nodes.wallBrightnessStatus.textContent,/failed/);
  assert.match(e.nodes.wallBrightnessStatus.textContent,expected);
  assert.equal(e.calls.filter(call=>call.method==='POST').length,postCount);
}
''')

    def test_pending_change_blocks_duplicate_submission_and_exposes_barrier(self):
        self.run_js(r'''
const e=environment(); e.render();
e.responses.push(e.fresh(),{command_id:123},e.fresh(128,{controller_state_revision:8,last_applied_command_id:123}));
const first=e.change(128); const barrier=e.api.settled;
assert.ok(barrier instanceof Promise || typeof barrier.then==='function');
assert.equal(e.control.disabled,true); assert.match(e.nodes.wallBrightnessStatus.textContent,/Applying/);
e.control.handlers.change(); assert.equal(e.api.settled,barrier);
await first; assert.equal(e.api.settled,null);
assert.equal(e.calls.filter(call=>call.method==='POST').length,1);
e.render(e.fresh(128),true); await e.change(26);
assert.equal(e.calls.filter(call=>call.method==='POST').length,1);
''')

"""Composer must observe the Active receipt's basis before the next Check."""
from pathlib import Path
import subprocess
import unittest


class ComposerActivationObservationTests(unittest.TestCase):
    def test_real_publication_queue_waits_for_status_and_retains_timeout_fence(self):
        script = Path('web/static/js/composer_slice.js').read_text()
        source = '\n'.join((
            script[script.index('function stableGalleryJson'):script.index('function galleryThumbnailKey')],
            script[script.index('async function flushPublication'):script.index('function submit(')],
            script[script.index('async function waitForExactActivation'):script.index('async function stopOutputNow')],
        ))
        javascript = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
let now = 0, revision = 4, visible = 4, checks = [], puts = 0, mode = 'lag';
let release, entered;
const sleeping = new Promise(resolve => { entered = resolve; });
const gate = new Promise(resolve => { release = resolve; });
const identity = revision => ({scene_identity: {digest: `scene-${revision}`, revision}, output: {brightness: 0}});
const observation = () => ({controller_session_id: 'session', controller_state_revision: visible, active_identity: identity(visible)});
const state = {publication: {inFlight: null, queued: null, afterStop: null, scheduled: false}, wall: {bootstrap: {capabilities: {server_actions: {activation_available: true}}}, observation: observation(), dirty: true}};
const nodes = {'#liveAction': {}, '#targetFps': {value: '150'}};
const receipts = new Map();
let firstReceipt;
const context = {
  state, assert, queueMicrotask, Date: {now: () => now}, JSON, Number,
  $: selector => nodes[selector], api: '/api/composer',
  boundedTargetFps: Number, browserSceneForWall: scene => scene,
  globalSettingsForWall: () => ({brightness: 0}), newUuid: () => `key-${puts}`,
  intentIsCurrent: () => true, renderStatus() {}, wallStatus: () => ({}),
  fetch: async () => ({ok: true, json: async () => ({})}),
  refreshWallStatus: async options => {
    assert.equal(options.preserveAuthored, true);
    state.wall.observation = observation();
    if (mode === 'restart') state.wall.observation.controller_session_id = 'new-session';
    if (mode === 'identity') state.wall.observation.active_identity = identity(999);
  },
  sleep: async ms => {
    now += ms;
    if (mode === 'lag') { entered(); await gate; visible = revision; mode = 'success'; }
    if (mode === 'success') visible = revision;
  },
  requestJson: async (url, options = {}) => {
    if (url.endsWith('/checks')) {
      checks.push(state.wall.observation.controller_state_revision);
      assert.equal(checks.at(-1), revision, 'Check must not bind to lagging status');
      return {check_token: 'checked', basis: {controller: {session_id: 'session', state_revision: revision}}};
    }
    if (options.method === 'PUT') {
      assert.equal(JSON.parse(options.body).expected_controller_state_revision, revision);
      puts += 1; revision += 1;
      const receipt = {activation_id: `activation-${puts}`, phase: mode === 'terminal' ? 'rolled_back' : 'active', error: 'Activation rolled back.', controller: {session_id: 'session', state_revision_after: revision}, requested_identity: identity(revision), observed_identity: identity(revision)};
      receipts.set(`/receipt/${puts}`, receipt);
      firstReceipt ||= receipt;
      return {activation_id: receipt.activation_id, status_url: `/receipt/${puts}`};
    }
    return receipts.get(url);
  },
};
vm.runInNewContext(process.argv[1], context);
const entry = name => ({scene: {name}, targetFps: 150, endpoint: '/scene', body: {}, resolve() {}, reject(error) { throw error; }});
(async () => {
  const first = entry('first'); state.publication.queued = first;
  const running = context.flushPublication();
  await Promise.race([sleeping, running.then(() => { throw new Error("publication released before status caught up"); })]);
  state.publication.queued = entry('newest');
  assert.equal(state.publication.inFlight, first);
  assert.deepEqual(checks, [4]); assert.equal(puts, 1);
  release(); await running;
  while (state.publication.inFlight || state.publication.scheduled) await new Promise(setImmediate);
  assert.deepEqual(checks, [4, 5]); assert.equal(puts, 2);
  assert.equal(visible, 6); assert.equal(state.wall.pendingObservation, null);

  // A timeout must remain a fence for the next queued/edit attempt, without
  // another Check/PUT or replay of the successful activation.
  state.wall.pendingObservation = firstReceipt; visible = 4; mode = 'timeout';
  const before = puts;
  await assert.rejects(context.guardedWallActivation({}, true, 150), /not available yet/);
  assert.equal(state.wall.pendingObservation, firstReceipt);
  await assert.rejects(context.guardedWallActivation({}, true, 150), /not available yet/);
  assert.equal(puts, before); assert.deepEqual(checks, [4, 5]);

  visible = 5; mode = 'identity';
  await assert.rejects(context.waitForActivationObservation(), /wall changed/);
  state.wall.pendingObservation = firstReceipt; mode = 'restart';
  await assert.rejects(context.waitForActivationObservation(), /wall restarted/);
  state.wall.pendingObservation = firstReceipt; visible = 6; mode = 'success';
  await assert.rejects(context.waitForActivationObservation(), /wall changed/);

  // Failed activation receipts are terminal, never an observation retry.
  state.wall.pendingObservation = null; visible = revision; mode = 'terminal';
  await assert.rejects(context.guardedWallActivation({}, true, 150), /Activation rolled back/);
  assert.equal(puts, before + 1); assert.equal(state.wall.pendingObservation, null);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run(['node', '-e', javascript, source], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        old_flow = source.replace(
            'state.wall.pendingObservation = await waitForExactActivation(accepted, checked.basis.controller.session_id);\n    await waitForActivationObservation();',
            'await waitForExactActivation(accepted, checked.basis.controller.session_id);\n    await refreshWallStatus({preserveAuthored: true});',
        )
        negative = subprocess.run(['node', '-e', javascript, old_flow], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(negative.returncode, 0)
        self.assertIn('publication released before status caught up', negative.stderr)

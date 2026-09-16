"""Composer must observe the resulting receipt basis before the next Check."""
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
  assert.equal(puts, before + 1); assert.equal(state.wall.pendingObservation.phase, 'rolled_back');
  await assert.rejects(context.guardedWallActivation({}, true, 150), /no complete observation basis/);
  assert.equal(puts, before + 1);
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

    def test_terminal_rollback_fences_newest_intent_without_retrying_failure(self):
        script = Path('web/static/js/composer_slice.js').read_text()
        source = '\n'.join((
            script[script.index('function stableGalleryJson'):script.index('function galleryThumbnailKey')],
            script[script.index('async function flushPublication'):script.index('function submit(')],
            script[script.index('async function waitForExactActivation'):script.index('async function stopOutputNow')],
        ))
        javascript = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
async function run(phase) {
  let now = 0, revision = 12, visible = 12, puts = 0, checks = [], mode = 'lag';
  let release, entered;
  const gate = new Promise(resolve => { release = resolve; });
  const waiting = new Promise(resolve => { entered = resolve; });
  const restored = {scene_identity: {revision: 2, digest: 'restored-circadian'}};
  const newest = {scene_identity: {revision: 2, digest: 'newest'}};
  const receipt = {activation_id: 'first', phase, error: 'original missing foreground ACK',
    controller: {session_id: 'session', state_revision_before: 12, state_revision_after: 13},
    requested_identity: {scene_identity: {revision: 2, digest: 'failed-aurora'}},
    observed_identity: restored, rollback: {result: 'succeeded'}, telemetry: {complete: true, fresh: true}};
  const state = {publication: {inFlight: null, queued: null, afterStop: null, scheduled: false},
    wall: {bootstrap: {capabilities: {server_actions: {activation_available: true}}}, dirty: true}};
  const context = {state, queueMicrotask, Date: {now: () => now}, JSON, Number,
    $: () => ({}), api: '/api/composer', renderStatus() {}, wallStatus: () => ({}), intentIsCurrent: () => true,
    browserSceneForWall: x => x, globalSettingsForWall: () => ({}), newUuid: () => 'id',
    fetch: async () => ({ok: true, json: async () => ({})}),
    refreshWallStatus: async () => { state.wall.observation = {
      controller_session_id: mode === 'restart' ? 'restarted' : 'session',
      controller_state_revision: visible, active_identity: visible >= 14 ? newest : restored}; },
    sleep: async ms => { now += ms;
      if (mode === 'lag') { entered(); await gate; visible = 13; mode = 'success'; }
      else if (mode === 'success') visible = revision;
    },
    requestJson: async (url, options = {}) => {
      if (url.endsWith('/checks')) {
        checks.push(state.wall.observation.controller_state_revision);
        assert.equal(checks.at(-1), revision, 'terminal rollback released a stale Check');
        return {check_token: 'token', basis: {controller: {session_id: 'session', state_revision: revision}}};
      }
      if (options.method === 'PUT') {
        assert.equal(JSON.parse(options.body).expected_controller_state_revision, revision);
        puts++; revision++;
        return {activation_id: puts === 1 ? 'first' : 'second', status_url: `/receipt/${puts}`};
      }
      if (url === '/receipt/1') return receipt;
      return {activation_id: 'second', phase: 'active', controller: {session_id: 'session', state_revision_after: revision}, requested_identity: newest, observed_identity: newest};
    },
  };
  vm.runInNewContext(process.argv[1], context);
  const entry = name => ({scene: {name}, targetFps: 150, endpoint: '/scene', body: {}, resolve() {}, reject(error) { throw error; }});
  state.publication.queued = entry('failed first');
  const running = context.flushPublication();
  const queued = entry('newest'); state.publication.queued = queued;
  await running;
  // A failed fence must not let an unresolved test disappear when Node exits.
  await Promise.race([waiting, new Promise((_, reject) => setImmediate(() => reject(new Error('terminal rollback released a stale Check'))))]);
  assert.equal(state.publication.inFlight, queued);
  assert.equal(state.wall.activationError, receipt.error);
  assert.equal(state.wall.retryBlocked, true);
  assert.deepEqual(checks, [12]); assert.equal(puts, 1);
  release();
  while (state.publication.inFlight || state.publication.scheduled) await new Promise(setImmediate);
  assert.deepEqual(checks, [12, 13]); assert.equal(puts, 2);
  assert.equal(state.wall.activationError, null);

  for (const change of [
    {observed_identity: null}, {observed_identity: {}},
    {controller: {...receipt.controller, state_revision_after: null}},
    {rollback: {result: 'failed'}}, {telemetry: {complete: false, fresh: true}},
    {telemetry: {complete: true, fresh: false}},
  ]) {
    state.wall.pendingObservation = {...receipt, ...change};
    await assert.rejects(context.guardedWallActivation({}, true, 150), /no complete observation basis/);
    assert.equal(puts, 2); assert.deepEqual(checks, [12, 13]);
  }
  state.wall.pendingObservation = receipt; visible = 12; mode = 'timeout';
  await assert.rejects(context.guardedWallActivation({}, true, 150), /not available yet/);
  assert.equal(state.wall.pendingObservation, receipt); assert.equal(puts, 2);
  mode = 'restart';
  await assert.rejects(context.guardedWallActivation({}, true, 150), /wall restarted/);
  assert.equal(puts, 2);

  // Positive controller evidence of rejection before mutation remains
  // recoverable by a fresh explicit edit, unlike an unresolved mutation.
  Object.assign(receipt, {phase: 'failed',
    error: 'durable queued activation rejected before mutation: stale basis',
    controller: {...receipt.controller, state_revision_after: null}, observed_identity: null,
    telemetry: {complete: false, fresh: false},
    rollback: {available: false, snapshot_id: null, result: null,
      error: 'no rollback authority was acquired before rejection'}});
  state.wall.pendingObservation = null;
  await assert.rejects(context.waitForExactActivation({activation_id: 'first', status_url: '/receipt/1'}, 'session'), /rejected before mutation/);
  assert.equal(state.wall.pendingObservation, null);
  visible = revision; mode = 'success';
  await context.guardedWallActivation({}, true, 150);
  assert.equal(puts, 3); assert.deepEqual(checks, [12, 13, 14]);
}
(async () => { for (const phase of ['timed_out', 'rolled_back', 'failed']) await run(phase); })()
  .catch(error => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run(['node', '-e', javascript, source], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        old_flow = source.replace('        state.wall.pendingObservation = rejectedBeforeMutation ? null : status;\n', '')
        negative = subprocess.run(['node', '-e', javascript, old_flow], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(negative.returncode, 0)
        self.assertIn('terminal rollback released a stale Check', negative.stderr)

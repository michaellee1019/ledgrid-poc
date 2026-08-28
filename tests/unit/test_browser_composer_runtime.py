"""Warm-worker, recovery, generation, and offline-manifest contracts."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from animation.browser_preview.python.runtime import (
    BrowserPreviewRuntime,
    MAX_RUNTIME_INSTANCES,
)
from tools.build_browser_offline_manifest import (
    CACHE_VERSION,
    PREVIOUS_CACHE_VERSION,
    build_manifest,
)


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_JS = ROOT / "web/static/js/composer_runtime.js"
WORKER_JS = ROOT / "web/static/js/composer_python_worker.js"
SERVICE_WORKER_JS = ROOT / "web/static/js/composer_service_worker.js"
OFFLINE_MANIFEST = (
    ROOT / "web/static/generated/composer/offline_assets.json"
)


class BrowserComposerRuntimeTests(unittest.TestCase):
    def test_python_runtime_bounds_instances_and_releases_replacements(self) -> None:
        runtime = BrowserPreviewRuntime()
        artifact = (ROOT / "tests/fixtures/installation_profile_v1.bin").read_bytes()
        digest = artifact[68:100].hex()
        geometry = {"width": 33, "height": 138}
        with tempfile.TemporaryDirectory() as temporary:
            profile_path = Path(temporary) / "profile.bin"
            profile_path.write_bytes(artifact)
            runtime.bind_installation_profile_path(str(profile_path), digest)
            for index in range(MAX_RUNTIME_INSTANCES):
                ready = runtime.initialize(
                    "rainbow",
                    "RainbowAnimation",
                    geometry,
                    {"brightness": 0.5},
                    instance_id=f"instance-{index}",
                    installation_profile_digest=digest,
                )
                self.assertEqual(ready["instanceCount"], index + 1)
                self.assertEqual(ready["maxInstances"], MAX_RUNTIME_INSTANCES)

            with self.assertRaisesRegex(RuntimeError, "instance limit reached"):
                runtime.initialize(
                    "rainbow", "RainbowAnimation", geometry,
                    instance_id="one-too-many",
                    installation_profile_digest=digest,
                )

            disposed = runtime.dispose_instance("instance-3")
            self.assertTrue(disposed["disposed"])
            self.assertEqual(disposed["maxInstances"], MAX_RUNTIME_INSTANCES)
            ready = runtime.initialize(
                "wave", "WaveAnimation", geometry, instance_id="replacement",
                installation_profile_digest=digest,
            )
            self.assertEqual(ready["instanceCount"], MAX_RUNTIME_INSTANCES)

    def test_browser_wrapper_reuses_one_worker_drops_stale_frames_and_recovers_once(self) -> None:
        script = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const workers = [];
let profileFetches = 0;

class FakeWorker {
  constructor(url, options) {
    this.url = url;
    this.options = options;
    this.listeners = {message: [], error: [], messageerror: []};
    this.messages = [];
    this.terminated = false;
    workers.push(this);
  }
  addEventListener(name, listener) { this.listeners[name].push(listener); }
  terminate() { this.terminated = true; }
  emit(name, value) { for (const listener of this.listeners[name]) listener(value); }
  postMessage(message) {
    this.messages.push(message);
    let response;
    let delay = 0;
    if (message.type === 'init') {
      response = {type: 'ready', requestId: message.requestId,
                  instanceId: message.instanceId, engine: 'python-pyodide-wasm',
                  width: 2, height: 3};
    } else if (message.type === 'render') {
      if (this.holdRenders) return;
      response = {type: 'frame', requestId: message.requestId,
                  instanceId: message.instanceId, generation: message.generation,
                  engine: 'python-pyodide-wasm', width: 2, height: 3,
                  pixels: new ArrayBuffer(18)};
      delay = message.generation === 1 ? 20 : 0;
    } else if (message.type === 'dispose') {
      response = {type: 'disposed', requestId: message.requestId,
                  instanceId: message.instanceId};
    } else {
      throw new Error(`unexpected fake-worker message ${message.type}`);
    }
    setTimeout(() => this.emit('message', {data: response}), delay);
  }
  crash() {
    this.emit('error', {message: 'evicted', preventDefault() {}});
  }
}

const context = {
  Worker: FakeWorker,
  setTimeout,
  clearTimeout,
  console,
  navigator: {},
  MessageChannel: undefined,
  URL,
  location: {href: 'https://example.invalid/composer', origin: 'https://example.invalid'},
  fetch: async (url) => {
    profileFetches += 1;
    const digest = String(url).match(/([12])\1{63}/)?.[0] || '';
    return {
      ok: true, status: 200, url: String(url),
      headers: {get(name) { return name === 'ETag' ? `"${digest}"` : null; }},
      async arrayBuffer() { return new ArrayBuffer(328); },
    };
  },
};
context.window = context;
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const {ComposerRuntime} = context.LEDGridComposerRuntime;
const plain = (value) => JSON.parse(JSON.stringify(value));
const component = {
  key: 'python:rainbow', plugin_id: 'rainbow', class_name: 'RainbowAnimation',
  browser_runtime: {
    supported: true, kind: 'python', engine: 'python-pyodide-wasm',
    worker_url: '/python-worker.js', asset_url: '/runtime.zip',
  },
};
const profile = {digest: '1'.repeat(64), artifactUrl: '/api/v1/installation-profiles/' + '1'.repeat(64) + '/artifact'};

(async () => {
  const first = new ComposerRuntime(component, {width: 2, height: 3}, {maxRestarts: 1, installationProfile: profile});
  const second = new ComposerRuntime(component, {width: 2, height: 3}, {installationProfile: profile});
  await first.init({brightness: 0.5});
  await second.init({brightness: 0.7});
  assert.strictEqual(profileFetches, 1, 'shared clients must reuse one controlled-page profile read');
  assert.strictEqual(workers.length, 1, 'Python clients must share one warm worker');
  const initIds = workers[0].messages.filter(x => x.type === 'init').map(x => x.instanceId);
  assert.strictEqual(new Set(initIds).size, 2, 'client instances must be scoped');
  assert.ok(
    workers[0].messages.filter(x => x.type === 'init')
      .every(x => x.installationProfileArtifact.bytes instanceof ArrayBuffer),
    'every worker initialization must receive controlled-page profile bytes',
  );

  const oldFrame = second.render(0, 0, {brightness: 0.2});
  const newFrame = second.render(0.1, 1, {brightness: 0.9});
  const frames = await Promise.all([oldFrame, newFrame]);
  assert.deepStrictEqual(frames.map(x => x.generation), [2, 2],
                         'both waiters must settle on the latest generation');

  workers[0].holdRenders = true;
  const nestedDraft = {brightness: 0.8, style: {palette: ['cyan', 'gold']}};
  const staleAfterCrash = second.render(0.2, 2, {brightness: 0.3, style: {palette: ['stale']}});
  const exactAfterCrash = second.render(0.3, 3, nestedDraft);
  nestedDraft.style.palette[0] = 'mutated-after-submit';
  workers[0].crash();
  const recovered = await Promise.all([staleAfterCrash, exactAfterCrash]);
  assert.deepStrictEqual(recovered.map(x => x.generation), [4, 4]);
  assert.strictEqual(workers.length, 2, 'one bounded worker restart expected');
  const recoveredInit = workers[1].messages.find(x => x.type === 'init' && x.params.brightness === 0.8);
  assert.deepStrictEqual(plain(recoveredInit.params.style.palette), ['cyan', 'gold'],
                         'recovery must restore the exact submitted draft snapshot');
  assert.strictEqual(profileFetches, 1, 'worker recovery must reuse the verified profile artifact');
  assert.deepStrictEqual(
    workers[1].messages.filter(x => x.type === 'render').map(x => x.generation),
    [4],
    'a stale render must not be replayed into the restored worker',
  );
  const healthy = ComposerRuntime.diagnostics().workers[0];
  assert.strictEqual(healthy.restarts, 1);
  assert.deepStrictEqual(plain(healthy.recovery), {
    state: 'healthy', attempts: 1, limit: 1, windowMs: 60000,
    exhausted: false, lastFault: 'evicted', lastFaultKind: 'worker-error',
  });

  workers[0].crash();
  const afterStaleWorkerEvent = await second.render(0.4, 4, {brightness: 0.85});
  assert.strictEqual(afterStaleWorkerEvent.generation, 5);
  assert.strictEqual(workers.length, 2, 'a stale terminated-worker event must not fault the replacement');

  workers[1].crash();
  await assert.rejects(
    second.render(0.5, 5, {brightness: 0.6}),
    /automatic recovery is limited to 1 restart within the recovery window/,
  );
  const exhausted = ComposerRuntime.diagnostics().workers[0].recovery;
  assert.deepStrictEqual(plain(exhausted), {
    state: 'exhausted', attempts: 1, limit: 1, windowMs: 60000,
    exhausted: true, lastFault: 'evicted', lastFaultKind: 'worker-error',
  });
  first.dispose();
  second.dispose();

  const alternateProfile = {
    digest: '2'.repeat(64),
    artifactUrl: '/api/v1/installation-profiles/' + '2'.repeat(64) + '/artifact',
  };
  const bounded = new ComposerRuntime(component, {width: 2, height: 3}, {
    maxRestarts: 999, restartWindowMs: 9999999, installationProfile: alternateProfile,
  });
  await bounded.init({brightness: 0.4});
  assert.strictEqual(profileFetches, 2, 'a distinct managed profile requires its own artifact read');
  const boundedRecovery = ComposerRuntime.diagnostics().workers[1].recovery;
  assert.strictEqual(boundedRecovery.limit, 3, 'caller options cannot make recovery unbounded');
  assert.strictEqual(boundedRecovery.windowMs, 300000, 'the restart accounting window is bounded');
  bounded.dispose();
  ComposerRuntime.shutdownSharedWorkers();
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            ["node", "-e", script, str(RUNTIME_JS)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_composed_python_batch_halves_bridges_and_recovers_both_instances(self) -> None:
        script = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const workers = [];
let profileFetches = 0;

class FakeWorker {
  constructor() {
    this.listeners = {message: [], error: [], messageerror: []};
    this.messages = [];
    this.heldBatch = null;
    this.terminated = false;
    workers.push(this);
  }
  addEventListener(name, listener) { this.listeners[name].push(listener); }
  terminate() { this.terminated = true; }
  emit(name, value) { for (const listener of this.listeners[name]) listener(value); }
  batchResponse(message) {
    const lengths = message.renders.map((render) => render.instanceId.endsWith('clock_overlay') ? 24 : 18);
    const pixels = new Uint8Array(lengths.reduce((total, length) => total + length, 0));
    const frames = [];
    let offset = 0;
    message.renders.forEach((render, index) => {
      pixels.fill(index + 1, offset, offset + lengths[index]);
      frames.push({
        instanceId: render.instanceId,
        generation: render.generation,
        byteOffset: offset,
        byteLength: lengths[index],
        width: 2,
        height: 3,
        frameFormat: index ? 'premultiplied-rgba' : 'rgb',
        role: index ? 'overlay' : 'background',
      });
      offset += lengths[index];
    });
    return {type: 'frameBatch', requestId: message.requestId, frames, pixels: pixels.buffer, engine: 'python-pyodide-wasm'};
  }
  releaseBatch() {
    const message = this.heldBatch;
    this.heldBatch = null;
    this.holdBatch = false;
    this.emit('message', {data: this.batchResponse(message)});
  }
  postMessage(message) {
    this.messages.push(message);
    let response;
    if (message.type === 'init') {
      response = {type: 'ready', requestId: message.requestId, instanceId: message.instanceId,
                  engine: 'python-pyodide-wasm', width: 2, height: 3};
    } else if (message.type === 'renderBatch') {
      if (this.holdBatch) { this.heldBatch = message; return; }
      response = this.batchResponse(message);
    } else if (message.type === 'render') {
      const clock = message.instanceId.endsWith('clock_overlay');
      response = {type: 'frame', requestId: message.requestId, instanceId: message.instanceId,
                  generation: message.generation, engine: 'python-pyodide-wasm', width: 2, height: 3,
                  pixels: new ArrayBuffer(clock ? 24 : 18),
                  frameFormat: clock ? 'premultiplied-rgba' : 'rgb'};
    } else if (message.type === 'dispose') {
      response = {type: 'disposed', requestId: message.requestId, instanceId: message.instanceId};
    } else {
      throw new Error(`unexpected fake-worker message ${message.type}`);
    }
    setTimeout(() => this.emit('message', {data: response}), 0);
  }
  crash() { this.emit('error', {message: 'batch evicted', preventDefault() {}}); }
}

const context = {
  Worker: FakeWorker, setTimeout, clearTimeout, console, navigator: {},
  MessageChannel: undefined, ArrayBuffer, Uint8Array, URL,
  location: {href: 'https://example.invalid/composer', origin: 'https://example.invalid'},
  fetch: async (url) => {
    profileFetches += 1;
    return {
      ok: true, status: 200, url: String(url),
      headers: {get(name) { return name === 'ETag' ? `"${'1'.repeat(64)}"` : null; }},
      async arrayBuffer() { return new ArrayBuffer(328); },
    };
  },
};
context.window = context;
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const {ComposerRuntime} = context.LEDGridComposerRuntime;
const profile = {digest: '1'.repeat(64), artifactUrl: '/profiles/' + '1'.repeat(64)};
const runtimeAsset = {
  supported: true, kind: 'python', engine: 'python-pyodide-wasm',
  worker_url: '/python-worker.js', asset_url: '/runtime.zip',
};
const background = {key: 'python:gradient', plugin_id: 'gradient', class_name: 'GradientAnimation', browser_runtime: runtimeAsset};
const clock = {key: 'python:clock_overlay', plugin_id: 'clock_overlay', class_name: 'ClockOverlayAnimation', browser_runtime: runtimeAsset};

function pair(runtime, frameIndex) {
  return runtime.renderInstances([
    {instanceId: 'primary', elapsed: frameIndex / 12, frameIndex, params: {speed: 0.7}},
    {instanceId: 'clock_overlay', elapsed: frameIndex / 12, frameIndex,
     params: {show_seconds: true}, wallTime: 1787774400 + frameIndex / 12},
  ]);
}

(async () => {
  const runtime = new ComposerRuntime(background, {width: 2, height: 3}, {
    maxRestarts: 1, installationProfile: profile,
  });
  await runtime.init({speed: 0.7});
  await runtime.initInstance(clock, {show_seconds: true}, 'clock_overlay');
  assert.strictEqual(profileFetches, 1, 'composed instances share one controlled-page profile read');
  assert.ok(
    workers[0].messages.filter(x => x.type === 'init')
      .every(x => x.installationProfileArtifact.bytes instanceof ArrayBuffer),
    'both composed instances receive the same verified profile artifact bytes',
  );

  const first = await pair(runtime, 0);
  assert.strictEqual(first.length, 2);
  assert.deepStrictEqual(Array.from(first[0].pixels), Array(18).fill(1));
  assert.deepStrictEqual(Array.from(first[1].pixels), Array(24).fill(2));
  assert.strictEqual(workers[0].messages.filter(x => x.type === 'renderBatch').length, 1,
                     'one composed frame must use one worker/Pyodide bridge');
  assert.strictEqual(workers[0].messages.filter(x => x.type === 'render').length, 0,
                     'the composed path must not serialize two individual bridges');
  const firstBatch = workers[0].messages.find(x => x.type === 'renderBatch');
  assert.deepStrictEqual(JSON.parse(JSON.stringify(firstBatch.renders.map(x => x.params))), [{}, {}],
                         'parameters already applied at init must not be reapplied per frame');

  await runtime.render(0.1, 1, {speed: 0.8, style: {palette: ['cyan', 'gold']}});
  let lastRender = workers[0].messages.filter(x => x.type === 'render').at(-1);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(lastRender.params)),
                         {speed: 0.8, style: {palette: ['cyan', 'gold']}},
                         'a real live change crosses the worker boundary exactly once');
  await runtime.render(0.2, 2, {speed: 0.8, style: {palette: ['cyan', 'gold']}});
  lastRender = workers[0].messages.filter(x => x.type === 'render').at(-1);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(lastRender.params)), {},
                         'an identical live update is an explicit no-op');

  workers[0].holdBatch = true;
  const staleBackground = pair(runtime, 1);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.ok(workers[0].heldBatch);
  await runtime.render(0.2, 2, {speed: 0.8});
  workers[0].releaseBatch();
  await assert.rejects(staleBackground, /obsolete or invalid composed frame/);

  workers[0].holdBatch = true;
  const staleClock = pair(runtime, 3);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.ok(workers[0].heldBatch);
  await runtime.renderInstance('clock_overlay', 0.4, 4, {show_seconds: false}, 1787774401);
  workers[0].releaseBatch();
  await assert.rejects(staleClock, /obsolete or invalid composed frame/);

  workers[0].holdBatch = true;
  const recovered = pair(runtime, 5);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.ok(workers[0].heldBatch);
  workers[0].crash();
  const recoveredFrames = await recovered;
  assert.strictEqual(recoveredFrames.length, 2);
  assert.strictEqual(workers.length, 2, 'the batch gets one bounded worker recovery');
  const recoveredInits = workers[1].messages.filter(x => x.type === 'init').map(x => x.instanceId);
  assert.strictEqual(new Set(recoveredInits).size, 2, 'recovery restores both exact instance descriptors');
  const recoveredBatch = workers[1].messages.find(x => x.type === 'renderBatch');
  assert.strictEqual(recoveredBatch.renders.length, 2);
  const backgroundInit = workers[1].messages.find(x => x.type === 'init' && x.instanceId.endsWith('.primary'));
  assert.strictEqual(backgroundInit.params.speed, 0.7,
                     'recovery retains the complete latest descriptor, not only its last delta');
  assert.strictEqual(
    JSON.stringify(Array.from(recoveredBatch.renders, x => x.instanceId).sort()),
    JSON.stringify(recoveredInits.slice().sort()),
  );
  runtime.dispose();
  ComposerRuntime.shutdownSharedWorkers();
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            ["node", "-e", script, str(RUNTIME_JS)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_service_worker_upgrade_stages_verified_generation_and_preserves_previous_on_failure(self) -> None:
        script = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const nodeCrypto = require('crypto');

const source = fs.readFileSync(process.argv[1], 'utf8');
const manifest = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const repoRoot = process.argv[3];
const assets = new Map(manifest.localAssets.map((asset) => [asset.url, asset]));
const profileBody = fs.readFileSync(`${repoRoot}/tests/fixtures/installation_profile_v1.bin`);
const profileDigest = profileBody.subarray(68, 100).toString('hex');
const profileUrl = `/api/v1/installation-profiles/${profileDigest}/artifact`;
const localPaths = {
  '/composer-service-worker.js': 'web/static/js/composer_service_worker.js',
  '/static/composer.webmanifest': 'web/static/composer.webmanifest',
  '/static/css/composer.css': 'web/static/css/composer.css',
  '/static/generated/composer/aurora_curtains_native.wasm': 'web/static/generated/composer/aurora_curtains_native.wasm',
  '/static/generated/composer/compiled_rainbow.wasm': 'web/static/generated/composer/compiled_rainbow.wasm',
  '/static/generated/composer/ledgrid_python_runtime.zip': 'web/static/generated/composer/ledgrid_python_runtime.zip',
  '/static/icons/composer-180.png': 'web/static/icons/composer-180.png',
  '/static/icons/composer-512.png': 'web/static/icons/composer-512.png',
  '/static/icons/composer.svg': 'web/static/icons/composer.svg',
  '/static/js/composer.js': 'web/static/js/composer.js',
  '/static/js/composer_compositor.js': 'web/static/js/composer_compositor.js',
  '/static/js/composer_native_worker.js': 'web/static/js/composer_native_worker.js',
  '/static/js/composer_python_worker.js': 'web/static/js/composer_python_worker.js',
  '/static/js/composer_runtime.js': 'web/static/js/composer_runtime.js',
  '/static/js/composer_state.js': 'web/static/js/composer_state.js',
};

class FakeResponse {
  constructor(body, options = {}) {
    this.body = Buffer.from(body || '');
    this.status = options.status || 200;
    this.ok = this.status >= 200 && this.status < 300;
    this.type = options.type || 'basic';
    const suppliedHeaders = options.headers || {};
    this.headers = typeof suppliedHeaders.get === 'function' ? suppliedHeaders : {
      get(name) {
        const key = Object.keys(suppliedHeaders).find(
          candidate => candidate.toLowerCase() === String(name).toLowerCase(),
        );
        return key ? suppliedHeaders[key] : null;
      },
    };
  }
  clone() { return new FakeResponse(this.body, {status: this.status, type: this.type, headers: this.headers}); }
  async arrayBuffer() { return this.body.buffer.slice(this.body.byteOffset, this.body.byteOffset + this.body.byteLength); }
  async json() { return JSON.parse(this.body.toString('utf8')); }
}

function cacheKey(value) { return typeof value === 'string' ? value : value.url; }

function makeHarness(failUrl = null) {
  const stores = new Map();
  const writes = [];
  const opened = [];
  const events = {};
  const priorName = `ledgrid-composer-shell-${manifest.previousCacheVersion}`;
  const priorRuntimeName = `${priorName}-python-runtime`;
  let networkDisabled = false;
  stores.set(priorName, new Map([['/prior-complete', new FakeResponse('prior-complete')]]));
  stores.set(priorRuntimeName, new Map([['https://runtime.invalid/prior', new FakeResponse('prior-runtime')]]));
  const caches = {
    async open(name) {
      opened.push(name);
      if (!stores.has(name)) stores.set(name, new Map());
      const store = stores.get(name);
      return {
        async put(key, response) { writes.push({name, key: cacheKey(key)}); store.set(cacheKey(key), response.clone()); },
        async match(key) { return store.get(cacheKey(key))?.clone(); },
        async keys() { return Array.from(store.keys()).map((url) => ({url})); },
      };
    },
    async delete(name) { return stores.delete(name); },
    async keys() { return Array.from(stores.keys()); },
    async match(key) {
      for (const store of stores.values()) {
        const response = store.get(cacheKey(key));
        if (response) return response.clone();
      }
      return undefined;
    },
  };
  const context = {
    URL, Set, Map, Promise, Uint8Array, ArrayBuffer, Request,
    Response: FakeResponse,
    crypto: nodeCrypto.webcrypto,
    caches,
    fetch: async (url) => {
      const key = cacheKey(url);
      if (networkDisabled) throw new Error(`offline fetch: ${key}`);
      if (key === failUrl) throw new Error(`injected fetch failure: ${key}`);
      if (key === '/composer') return new FakeResponse('<html>composer</html>');
      if (key === `https://example.invalid${profileUrl}` || key === profileUrl) {
        return new FakeResponse(profileBody, {headers: {ETag: `"${profileDigest}"`}});
      }
      if (key === '/static/generated/composer/offline_assets.json') {
        return new FakeResponse(fs.readFileSync(process.argv[2]));
      }
      const relative = localPaths[key];
      if (!relative) throw new Error(`unexpected fetch: ${key}`);
      return new FakeResponse(fs.readFileSync(`${repoRoot}/${relative}`));
    },
  };
  context.self = {
    location: {origin: 'https://example.invalid'},
    clients: {async claim() {}},
    async skipWaiting() {},
    addEventListener(name, handler) { events[name] = handler; },
  };
  vm.runInNewContext(source, context);
  return {
    events, stores, writes, opened, priorName, priorRuntimeName,
    setOffline(value) { networkDisabled = Boolean(value); },
  };
}

async function dispatch(handler) {
  let promise;
  handler({waitUntil(value) { promise = Promise.resolve(value); }});
  return promise;
}

async function message(harness, data) {
  let promise;
  let delivered;
  harness.events.message({
    data,
    ports: [{postMessage(value) { delivered = value; }}],
    waitUntil(value) { promise = Promise.resolve(value); },
  });
  await promise;
  return delivered;
}

(async () => {
  const failing = makeHarness('/static/js/composer_state.js');
  await assert.rejects(dispatch(failing.events.install), /injected fetch failure/);
  assert.strictEqual((await failing.stores.get(failing.priorName).get('/prior-complete').arrayBuffer()).byteLength, 14);
  assert.ok(failing.stores.has(failing.priorRuntimeName), 'prior runtime cache must survive');
  assert.ok(failing.opened.some((name) => name.endsWith('-staging')), 'upgrade must use an isolated staging cache');
  assert.ok(!failing.writes.some((item) => item.name === `ledgrid-composer-shell-${manifest.cacheVersion}`),
            'a failed fetch must never write into the final generation');
  assert.ok(!failing.stores.has(`ledgrid-composer-shell-${manifest.cacheVersion}`));

  const currentName = `ledgrid-composer-shell-${manifest.cacheVersion}`;
  const tampered = makeHarness();
  await dispatch(tampered.events.install);
  tampered.stores.get(currentName).set('/static/js/composer_state.js', new FakeResponse('tampered'));
  await assert.rejects(dispatch(tampered.events.activate), /failed verification/);
  assert.ok(tampered.stores.has(tampered.priorName),
            'activation verification failure must preserve the prior complete generation');

  const successful = makeHarness();
  await dispatch(successful.events.install);
  assert.ok(successful.stores.has(successful.priorName), 'prior cache remains active until activation');
  const current = successful.stores.get(currentName);
  assert.ok(current, 'verified generation must be promoted after complete staging');
  for (const [url, declared] of assets) {
    const response = current.get(url);
    assert.ok(response, `missing promoted asset ${url}`);
    const body = Buffer.from(await response.arrayBuffer());
    assert.strictEqual(body.byteLength, declared.bytes);
    assert.strictEqual(nodeCrypto.createHash('sha256').update(body).digest('hex'), declared.sha256);
  }
  await dispatch(successful.events.activate);
  assert.ok(!successful.stores.has(successful.priorName));
  assert.ok(!successful.stores.has(successful.priorRuntimeName));
  assert.ok(successful.stores.has(currentName));
  assert.ok(!Array.from(successful.stores.keys()).some((name) => name.endsWith('-staging')));

  const artifactRequest = {
    type: 'INSTALLATION_PROFILE_ARTIFACT',
    digest: profileDigest,
    artifactUrl: `https://example.invalid${profileUrl}`,
  };
  const firstProfile = await message(successful, artifactRequest);
  assert.strictEqual(firstProfile.type, 'INSTALLATION_PROFILE_ARTIFACT');
  assert.strictEqual(firstProfile.digest, profileDigest);
  assert.deepStrictEqual(Buffer.from(firstProfile.bytes), profileBody);
  successful.setOffline(true);
  const replayedProfile = await message(successful, artifactRequest);
  assert.strictEqual(replayedProfile.type, 'INSTALLATION_PROFILE_ARTIFACT');
  assert.deepStrictEqual(Buffer.from(replayedProfile.bytes), profileBody,
                         'the exact verified profile must replay without network');
  const rejectedProfile = await message(successful, {
    ...artifactRequest,
    digest: 'f'.repeat(64),
  });
  assert.strictEqual(rejectedProfile.type, 'INSTALLATION_PROFILE_ARTIFACT_ERROR');
  assert.match(rejectedProfile.reason, /selected same-origin digest/);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        completed = subprocess.run(
            [
                "node",
                "-e",
                script,
                str(SERVICE_WORKER_JS),
                str(OFFLINE_MANIFEST),
                str(ROOT),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_worker_protocol_is_generation_aware_and_prepares_all_packages(self) -> None:
        worker = WORKER_JS.read_text(encoding="utf-8")
        runtime = RUNTIME_JS.read_text(encoding="utf-8")
        composer = (ROOT / "web/static/js/composer.js").read_text(encoding="utf-8")
        self.assertIn("latestRenderGeneration", worker)
        self.assertIn("type: 'obsolete'", worker)
        self.assertIn("generation <", worker)
        self.assertIn("message.type === 'prepare'", worker)
        self.assertIn("Object.freeze(['numpy', 'pillow'])", worker)
        self.assertIn("message.type === 'renderBatch'", worker)
        self.assertIn("render_batch_json", worker)
        self.assertIn("message.installationProfileArtifact", worker)
        self.assertIn(
            "const resultJson = pyodide.runPython(\n"
            "        '_ledgrid_browser_runtime.render_batch_json(_ledgrid_payload_json)'",
            worker,
        )
        self.assertIn("renderInstances(requests)", runtime)
        self.assertIn("await runtime.renderInstances([", composer)

    def test_offline_manifest_is_reproducible_and_pins_every_local_digest(self) -> None:
        committed = json.loads(OFFLINE_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(committed, build_manifest(ROOT))
        self.assertEqual(committed["cacheVersion"], CACHE_VERSION)
        self.assertEqual(
            committed["previousCacheVersion"], PREVIOUS_CACHE_VERSION
        )
        self.assertFalse(committed["pythonRuntime"]["selfHosted"])
        self.assertEqual(
            committed["pythonRuntime"]["integrity"],
            "sha256-observed-and-reverified",
        )
        for asset in committed["localAssets"]:
            self.assertRegex(asset["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(asset["bytes"], 0)

    def test_service_worker_readiness_requires_verified_shell_catalog_and_python(self) -> None:
        source = SERVICE_WORKER_JS.read_text(encoding="utf-8")
        self.assertIn(f"const CACHE_VERSION = '{CACHE_VERSION}'", source)
        self.assertIn(
            f"const PREVIOUS_CACHE_VERSION = '{PREVIOUS_CACHE_VERSION}'", source
        )
        self.assertIn("const CACHE_NAME = `${CACHE_PREFIX}${CACHE_VERSION}`", source)
        self.assertIn("const STAGING_CACHE_NAME = `${CACHE_NAME}-staging`", source)
        self.assertIn("installVersionedShell", source)
        self.assertIn("Offline asset digest mismatch", source)
        self.assertIn("promoteVerifiedShell", source)
        self.assertIn("verifyCachedShell", source)
        self.assertIn("name !== RUNTIME_CACHE_NAME", source)
        self.assertIn("PYTHON_RUNTIME_READY", source)
        self.assertIn("OFFLINE_STATUS", source)
        self.assertIn("readyOffline: true", source)
        self.assertIn("responseDigest(bootstrap)", source)
        self.assertIn("Python runtime asset changed", source)
        self.assertIn("INSTALLATION_PROFILE_ARTIFACT", source)
        self.assertIn("deliverInstallationProfileArtifact", source)
        self.assertIn("readyOffline: false", source)
        self.assertIn("name.startsWith(CACHE_PREFIX)", source)
        self.assertIn("names.filter", source)


if __name__ == "__main__":
    unittest.main()

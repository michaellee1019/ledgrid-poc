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
from tools.build_browser_offline_manifest import CACHE_VERSION, build_manifest


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
};
context.window = context;
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const {ComposerRuntime} = context.LEDGridComposerRuntime;
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
  assert.strictEqual(workers.length, 1, 'Python clients must share one warm worker');
  const initIds = workers[0].messages.filter(x => x.type === 'init').map(x => x.instanceId);
  assert.strictEqual(new Set(initIds).size, 2, 'client instances must be scoped');

  const oldFrame = second.render(0, 0, {brightness: 0.2});
  const newFrame = second.render(0.1, 1, {brightness: 0.9});
  const frames = await Promise.all([oldFrame, newFrame]);
  assert.deepStrictEqual(frames.map(x => x.generation), [2, 2],
                         'both waiters must settle on the latest generation');

  workers[0].crash();
  const recovered = await second.render(0.2, 2, {brightness: 0.8});
  assert.strictEqual(recovered.generation, 3);
  assert.strictEqual(workers.length, 2, 'one bounded worker restart expected');
  assert.strictEqual(ComposerRuntime.diagnostics().workers[0].restarts, 1);

  workers[1].crash();
  await assert.rejects(
    second.render(0.3, 3, {brightness: 0.6}),
    /automatic recovery is limited to 1 restart/,
  );
  first.dispose();
  second.dispose();
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

    def test_worker_protocol_is_generation_aware_and_prepares_all_packages(self) -> None:
        worker = WORKER_JS.read_text(encoding="utf-8")
        self.assertIn("latestRenderGeneration", worker)
        self.assertIn("type: 'obsolete'", worker)
        self.assertIn("generation <", worker)
        self.assertIn("message.type === 'prepare'", worker)
        self.assertIn("Object.freeze(['numpy', 'pillow'])", worker)

    def test_offline_manifest_is_reproducible_and_pins_every_local_digest(self) -> None:
        committed = json.loads(OFFLINE_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(committed, build_manifest(ROOT))
        self.assertEqual(committed["cacheVersion"], CACHE_VERSION)
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
        self.assertIn("const CACHE_NAME = `${CACHE_PREFIX}${CACHE_VERSION}`", source)
        self.assertNotIn("`${CACHE_PREFIX}v13`", source)
        self.assertIn("installVersionedShell", source)
        self.assertIn("Offline asset digest mismatch", source)
        self.assertIn("await caches.delete(CACHE_NAME)", source)
        self.assertIn("name !== RUNTIME_CACHE_NAME", source)
        self.assertIn("PYTHON_RUNTIME_READY", source)
        self.assertIn("OFFLINE_STATUS", source)
        self.assertIn("readyOffline: true", source)
        self.assertIn("responseDigest(bootstrap)", source)
        self.assertIn("Python runtime asset changed", source)
        self.assertIn("readyOffline: false", source)
        self.assertIn("name.startsWith(CACHE_PREFIX)", source)
        self.assertIn("names.filter", source)


if __name__ == "__main__":
    unittest.main()

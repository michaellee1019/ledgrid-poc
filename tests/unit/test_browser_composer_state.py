"""Browser composer draft/check state contract tests."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STATE_SOURCE = ROOT / "web/static/js/composer_state.js"
COMPOSER_SOURCE = ROOT / "web/static/js/composer.js"
OPERATIONS_SOURCE = ROOT / "web/static/js/composer-operations.js"


def _run_state_script(body: str) -> dict:
    executable = shutil.which("node")
    if executable is None:
        raise unittest.SkipTest("node unavailable; composer state tests need JavaScript")
    script = f"""
const fs = require('fs');
const vm = require('vm');
vm.runInThisContext(fs.readFileSync({json.dumps(str(STATE_SOURCE))}, 'utf8'));
const state = globalThis.LEDGridComposerState;
{body}
"""
    completed = subprocess.run(
        [executable, "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _run_operations_script(body: str) -> dict:
    executable = shutil.which("node")
    if executable is None:
        raise unittest.SkipTest("node unavailable; composer operations tests need JavaScript")
    script = f"""
const fs = require('fs');
const vm = require('vm');
vm.runInThisContext(fs.readFileSync({json.dumps(str(OPERATIONS_SOURCE))}, 'utf8'));
const operations = globalThis.LEDGridComposerOperations;
{body}
"""
    completed = subprocess.run(
        [executable, "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


@unittest.skipUnless(shutil.which("node"), "node unavailable")
class BrowserComposerStateTests(unittest.TestCase):
    def test_latest_state_queue_serializes_coalesces_and_clones_intents(self) -> None:
        result = _run_state_script("""
const queue = state.createLatestStateQueue();
const original = {value: 0, nested: {label: 'captured'}};
queue.enqueue(original);
const first = queue.begin();
original.nested.label = 'mutated';
for (let value = 1; value <= 25; value += 1) queue.enqueue({value});
const blocked = queue.begin();
const firstWasCurrent = queue.finish(first, {state: 'active'});
const latest = queue.begin();
const latestWasCurrent = queue.finish(latest, {state: 'active', message: 'latest'});
queue.enqueue({value: 26});
const invalidated = queue.begin();
queue.enqueue({value: 27});
queue.invalidate({state: 'paused', message: 'reconnected'});
const invalidatedWasCurrent = queue.finish(invalidated, {state: 'active'});
console.log(JSON.stringify({
  first: first.intent,
  blocked,
  firstWasCurrent,
  latest: latest.intent,
  latestWasCurrent,
  outcome: queue.outcome(),
  invalidatedWasCurrent,
  hasQueued: queue.hasQueued(),
  hasInFlight: queue.hasInFlight(),
}));
""")

        self.assertEqual(result["first"], {"value": 0, "nested": {"label": "captured"}})
        self.assertIsNone(result["blocked"])
        self.assertFalse(result["firstWasCurrent"])
        self.assertEqual(result["latest"], {"value": 25})
        self.assertTrue(result["latestWasCurrent"])
        self.assertEqual(result["outcome"], {"state": "paused", "message": "reconnected"})
        self.assertFalse(result["invalidatedWasCurrent"])
        self.assertFalse(result["hasQueued"])
        self.assertFalse(result["hasInFlight"])

    def test_output_power_presentation_states_are_revision_qualified(self) -> None:
        result = _run_operations_script("""
const common = {provider: 'a'.repeat(32), revision: 41};
console.log(JSON.stringify({
  on: operations.outputPowerState({...common, desired: true, observed: true}),
  off: operations.outputPowerState({...common, desired: false, observed: false}),
  pending: operations.outputPowerState({...common, desired: false, observed: true, pending: true}),
  failed: operations.outputPowerState({...common, desired: false, observed: true, outcome: {state: 'failed', message: 'receiver rejected apply'}}),
  stale: operations.outputPowerState({desired: true, observed: false}),
}));
""")

        self.assertEqual(
            {name: value["state"] for name, value in result.items()},
            {
                "on": "on", "off": "off", "pending": "pending",
                "failed": "failed", "stale": "stale",
            },
        )
        self.assertEqual(result["pending"]["desired"], False)
        self.assertTrue(result["pending"]["observed"])
        self.assertEqual(result["failed"]["message"], "receiver rejected apply")
        self.assertEqual(result["on"]["revision"], 41)
        self.assertIsNone(result["stale"]["revision"])

    def test_terminal_power_activation_retains_failed_outcome_until_next_edit(self) -> None:
        source = COMPOSER_SOURCE.read_text(encoding="utf-8")
        terminal = source[source.index("['rolled_back', 'failed', 'timed_out']"):]
        terminal = terminal[:terminal.index("} else {")]
        self.assertIn("state.globalSettings.reconciliation = {", terminal)
        self.assertIn("state: 'failed'", terminal)
        self.assertLess(
            terminal.index("state.globalSettings.reconciliation = {"),
            terminal.index("state.globalSettings.powerActivation = null"),
        )
        self.assertIn("state.globalSettings.reconciliation = null", source)
    def test_numeric_normalization_clamps_rounds_and_uses_one_step_grid(self) -> None:
        result = _run_state_script("""
console.log(JSON.stringify({
  typed: state.normalizeNumber('0.26', 'float', {min: 0.1, max: 1, step: 0.1}),
  range: state.normalizeNumber(0.30000000000000004, 'float', {min: 0.1, max: 1, step: 0.1}),
  clamped: state.normalizeNumber(90, 'float', {min: 0.1, max: 5, step: 0.1}),
  integer: state.normalizeNumber(2.6, 'integer', {min: 0, max: 4, step: 1}),
  fallback: state.normalizeNumber('', 'float', {min: 0.1, max: 5, step: 0.1, default: 1.2}),
  display: state.formatNumber(0.3, 0.01),
}));
""")

        self.assertEqual(result, {
            "typed": 0.3,
            "range": 0.3,
            "clamped": 5,
            "integer": 3,
            "fallback": 0.1,
            "display": "0.30",
        })

    def test_metric_stats_order_heavy_tail_percentiles_upward(self) -> None:
        result = _run_state_script("""
const ordinary = state.orderedMetricStats([1, 2, 3, 4]);
const heavyTail = state.orderedMetricStats([...Array(19).fill(0), 100]);
let malformed = null;
try { state.orderedMetricStats([1, Number.NaN]); } catch (error) { malformed = error.message; }
console.log(JSON.stringify({ordinary, heavyTail, malformed}));
""")

        self.assertEqual(result["ordinary"], {
            "mean": 2.5,
            "p95": 4,
            "p99": 4,
            "max": 4,
        })
        self.assertEqual(result["heavyTail"], {
            "mean": 5,
            "p95": 5,
            "p99": 100,
            "max": 100,
        })
        self.assertIn("finite numbers", result["malformed"])

    def test_render_p95_is_advisory_and_never_blocks_activation(self) -> None:
        result = _run_state_script("""
console.log(JSON.stringify({
  fast: state.advisoryRenderStatus(2, 10),
  caution: state.advisoryRenderStatus(6, 10),
  overBudget: state.advisoryRenderStatus(40, 10),
}));
""")

        self.assertEqual(result, {
            "fast": "pass",
            "caution": "warn",
            "overBudget": "warn",
        })

    def test_check_binding_covers_generation_runtime_digest_and_geometry(self) -> None:
        result = _run_state_script("""
const component = {
  key: 'receiver_native:aurora',
  browser_runtime: {supported: true, asset_digest: 'wasm-123'},
  browser_capabilities: {
    previewable: true, saveable: true, activation_ready: false,
    reason: 'Managed receiver identity is unavailable.'
  }
};
const wallSettings = {vibeId: 'neutral', brightness: 128, targetFps: 30, speedMultiplier: 1};
const first = state.checkBinding(7, component, {strip_count: 33, leds_per_strip: 138, total_leds: 4554}, wallSettings, 'profile-a');
const next = state.checkBinding(8, component, {strip_count: 33, leds_per_strip: 138, total_leds: 4554}, wallSettings, 'profile-a');
const changedWall = state.checkBinding(7, component, {strip_count: 33, leds_per_strip: 138, total_leds: 4554}, {...wallSettings, brightness: 64}, 'profile-a');
const changedProfile = state.checkBinding(7, component, {strip_count: 33, leds_per_strip: 138, total_leds: 4554}, wallSettings, 'profile-b');
console.log(JSON.stringify({
  first,
  same: state.sameCheckBinding(first, {...first}),
  stale: state.sameCheckBinding(first, next),
  changedWall: state.sameCheckBinding(first, changedWall),
  changedProfile: state.sameCheckBinding(first, changedProfile),
  capability: state.capability(component),
}));
""")

        self.assertEqual(result["first"]["draftGeneration"], 7)
        self.assertEqual(result["first"]["runtimeDigest"], "wasm-123")
        self.assertEqual(result["first"]["geometry"]["totalLeds"], 4554)
        self.assertTrue(result["same"])
        self.assertFalse(result["stale"])
        self.assertFalse(result["changedWall"])
        self.assertFalse(result["changedProfile"])
        self.assertEqual(result["first"]["installationProfileDigest"], "profile-a")
        self.assertFalse(result["capability"]["activationReady"])
        self.assertIn("identity", result["capability"]["reason"])

    def test_local_profile_comes_from_static_catalog_not_server_actions(self) -> None:
        result = _run_state_script("""
const digest = 'c'.repeat(64);
const local = state.localInstallationProfile({
  installation_profile: {
    digest,
    artifact_url: `/static/generated/composer/installation_profile_${digest}.bin`,
    authority: 'bundled',
  },
  capabilities: {server_actions: {installation_profile_artifact_url: null}},
});
const absent = state.localInstallationProfile({
  installation_profile: {digest, artifact_url: null},
  capabilities: {
    server_actions: {
      installation_profile_artifact_url: `/api/v1/installation-profiles/${digest}/artifact`,
    },
  },
});
const empty = state.localInstallationProfile({
  installation_profile: {digest: '0'.repeat(64), artifact_url: '/static/profile.bin'},
});
console.log(JSON.stringify({local, absent, empty}));
""")

        self.assertEqual(result["local"], {
            "digest": "c" * 64,
            "artifactUrl": (
                "/static/generated/composer/installation_profile_"
                f"{'c' * 64}.bin"
            ),
        })
        self.assertIsNone(result["absent"])
        self.assertIsNone(result["empty"])

    def test_local_library_favorites_are_provider_qualified_and_sorted(self) -> None:
        result = _run_state_script("""
const first = {provider: 'python', component: 'aurora', preset: 'night'};
const collision = {provider: 'receiver_native', component: 'aurora', preset: 'night'};
const favorites = state.toggleLibraryFavorite([collision, first, first], first);
const added = state.toggleLibraryFavorite(favorites, {provider: 'python', component: 'aurora', preset: null});
console.log(JSON.stringify({
  favorites,
  added,
  firstKey: state.librarySelectionKey(first),
  collisionKey: state.librarySelectionKey(collision),
}));
""")

        self.assertEqual(result["favorites"], [
            {"provider": "receiver_native", "component": "aurora", "preset": "night"},
        ])
        self.assertEqual(result["added"], [
            {"provider": "python", "component": "aurora", "preset": None},
            {"provider": "receiver_native", "component": "aurora", "preset": "night"},
        ])
        self.assertNotEqual(result["firstKey"], result["collisionKey"])

    def test_local_library_recents_are_capped_and_replay_exact_catalog_identity(self) -> None:
        result = _run_state_script("""
const entries = Array.from({length: 14}, (_unused, index) => ({
  provider: index % 2 ? 'python' : 'receiver_native', component: `item-${index}`, preset: index % 3 ? null : 'look',
}));
const recents = entries.reduce((current, entry) => state.recordLibraryRecent(current, entry), []);
const repeated = state.recordLibraryRecent(recents, entries[4]);
const components = [
  {provider: 'python', plugin_id: 'same', role: 'background', presets: [{preset_id: 'warm'}]},
  {provider: 'receiver_native', plugin_id: 'same', role: 'background', presets: [{preset_id: 'warm'}]},
];
const resolved = state.resolveLibrarySelection({provider: 'receiver_native', component: 'same', preset: 'warm'}, components);
console.log(JSON.stringify({
  limit: state.LIBRARY_RECENTS_LIMIT,
  recents, repeated,
  resolved: {provider: resolved.component.provider, plugin: resolved.component.plugin_id, presetIndex: resolved.presetIndex},
}));
""")

        self.assertEqual(result["limit"], 12)
        self.assertEqual(len(result["recents"]), 12)
        self.assertEqual(len(result["repeated"]), 12)
        self.assertEqual(result["repeated"][0]["component"], "item-4")
        self.assertEqual(result["resolved"], {
            "provider": "receiver_native", "plugin": "same", "presetIndex": 0,
        })

    def test_library_discovery_unifies_renderer_and_preset_queries_without_id_collisions(self) -> None:
        result = _run_state_script("""
const components = [
  {
    provider: 'python', plugin_id: 'aurora', key: 'python:aurora', role: 'background',
    name: 'Aurora', description: 'Slow northern light folds', tags: ['light'],
    browser_runtime: {kind: 'python', supported: true},
    presets: [{key: 'night', name: 'Polar Night', category: 'Night', tags: ['dim']}],
  },
  {
    provider: 'receiver_native', plugin_id: 'aurora', key: 'receiver_native:aurora', role: 'background',
    name: 'Aurora Native', description: 'Receiver-native aurora',
    browser_runtime: {kind: 'native', supported: true},
    presets: [{key: 'showcase', name: 'Solar Wind', category: 'Showcase', tags: ['bright']}],
  },
  {provider: 'python', plugin_id: 'clock', role: 'overlay', name: 'Clock', browser_runtime: {supported: true}},
];
const entries = state.libraryDiscoveryEntries(components);
const favorite = {provider: 'receiver_native', component: 'aurora', preset: 'showcase'};
const recent = {provider: 'python', component: 'aurora', preset: 'night'};
console.log(JSON.stringify({
  limit: state.LIBRARY_DISCOVERY_BATCH_SIZE,
  kinds: entries.map((entry) => entry.kind),
  categories: state.libraryDiscoveryCategories(entries),
  query: state.filterLibraryDiscoveryEntries(entries, {query: 'dim'}).map((entry) => entry.key),
  rendererFirstQuery: state.filterLibraryDiscoveryEntries(entries, {query: 'aurora'}).map((entry) => entry.kind),
  native: state.filterLibraryDiscoveryEntries(entries, {runtime: 'native'}).map((entry) => entry.key),
  favorites: state.filterLibraryDiscoveryEntries(entries, {saved: 'favorites', favorites: [favorite]}).map((entry) => entry.key),
  recent: state.filterLibraryDiscoveryEntries(entries, {saved: 'recent', recents: [recent, favorite]}).map((entry) => entry.key),
}));
""")

        self.assertEqual(result["limit"], 24)
        self.assertEqual(result["kinds"], ["renderer", "renderer", "preset", "preset"])
        self.assertEqual(result["categories"], ["Night", "Showcase"])
        self.assertEqual(result["query"], ['["python","aurora","night"]'])
        self.assertEqual(result["rendererFirstQuery"], ["renderer", "renderer", "preset", "preset"])
        self.assertEqual(result["native"], [
            '["receiver_native","aurora",""]',
            '["receiver_native","aurora","showcase"]',
        ])
        self.assertEqual(result["favorites"], ['["receiver_native","aurora","showcase"]'])
        self.assertEqual(result["recent"], [
            '["python","aurora","night"]',
            '["receiver_native","aurora","showcase"]',
        ])

    def test_library_discovery_classifies_non_show_material_and_hides_it_by_default(self) -> None:
        result = _run_state_script("""
const components = [
  {
    provider: 'python', plugin_id: 'aurora', key: 'python:aurora', role: 'background',
    name: 'Aurora', browser_runtime: {kind: 'python', supported: true},
    presets: [{preset_id: 'night', name: 'Polar Night', tags: ['show']}],
  },
  {
    provider: 'python', plugin_id: 'simple_test', key: 'python:simple_test', role: 'background',
    name: 'Simple Test', browser_runtime: {kind: 'python', supported: true},
    presets: [{preset_id: 'fixture', name: 'Fixture'}],
  },
  {
    provider: 'python', plugin_id: 'wall_reference', key: 'python:wall_reference', role: 'background',
    name: 'Wall Calibration Reference', browser_runtime: {kind: 'python', supported: true},
    presets: [{preset_id: 'globe', name: 'Globe map', discovery_classification: 'calibration'}],
  },
  {
    provider: 'receiver_native', plugin_id: 'offline', key: 'receiver_native:offline', role: 'background',
    name: 'Offline', browser_runtime: {kind: 'native', supported: false},
  },
];
const entries = state.libraryDiscoveryEntries(components);
console.log(JSON.stringify({
  schemaVersions: [...new Set(entries.map((entry) => entry.schemaVersion))],
  classifications: entries.map((entry) => [entry.key, entry.classification, entry.eligible]),
  defaultEntries: state.filterLibraryDiscoveryEntries(entries, {}).map((entry) => entry.key),
  testEntries: state.filterLibraryDiscoveryEntries(entries, {classification: 'test', includeIneligible: true}).map((entry) => entry.key),
  calibrationEntries: state.filterLibraryDiscoveryEntries(entries, {classification: 'calibration', includeIneligible: true}).map((entry) => entry.key),
  presetKeyQuery: state.filterLibraryDiscoveryEntries(entries, {query: 'night'}).map((entry) => entry.key),
}));
""")

        self.assertEqual(result["schemaVersions"], [1])
        classifications = dict((key, (classification, eligible)) for key, classification, eligible in result["classifications"])
        self.assertEqual(classifications['["python","simple_test",""]'], ("test", False))
        self.assertEqual(classifications['["python","wall_reference","globe"]'], ("calibration", False))
        self.assertEqual(classifications['["receiver_native","offline",""]'], ("show", False))
        self.assertEqual(result["defaultEntries"], [
            '["python","aurora",""]',
            '["python","aurora","night"]',
        ])
        self.assertEqual(result["testEntries"], [
            '["python","simple_test","fixture"]',
            '["python","simple_test",""]',
        ])
        self.assertEqual(result["calibrationEntries"], [
            '["python","wall_reference","globe"]',
            '["python","wall_reference",""]',
        ])
        self.assertEqual(result["presetKeyQuery"], ['["python","aurora","night"]'])

    def test_activation_accepts_current_cautions_but_rejects_failures_and_stale_checks(self) -> None:
        result = _run_state_script("""
const component = {key: 'host_python:solid', browser_runtime: {digest: 'runtime-1'}};
const current = state.checkBinding(4, component, {strip_count: 33, leds_per_strip: 138, total_leds: 4554});
const stale = state.checkBinding(3, component, {strip_count: 33, leds_per_strip: 138, total_leds: 4554});
console.log(JSON.stringify({
  pass: state.checkAllowsActivation({status: 'pass', binding: current}, current),
  warn: state.checkAllowsActivation({status: 'warn', binding: current}, current),
  fail: state.checkAllowsActivation({status: 'fail', binding: current}, current),
  stale: state.checkAllowsActivation({status: 'pass', binding: stale}, current),
  missing: state.checkAllowsActivation(null, current),
}));
""")

        self.assertEqual(result, {
            "pass": True,
            "warn": True,
            "fail": False,
            "stale": False,
            "missing": False,
        })

    def test_wall_reconciliation_requires_a_fresh_exact_newer_acknowledgement(self) -> None:
        result = _run_state_script("""
const desired = {vibeId: 'cozy', brightness: 96, targetFps: 120, speedMultiplier: 1.5};
const pending = state.createWallReconciliation({
  provider: 'a'.repeat(32), revision: 40, desired, issuedAt: 1000,
});
const acknowledgement = state.reconcileWallObservation(pending, {
  provider: 'a'.repeat(32), revision: 41, observed: {...desired},
  observedAt: 1001, fresh: true,
}, 1010);
const stale = state.reconcileWallObservation(pending, {
  provider: 'a'.repeat(32), revision: 40, observed: {...desired},
  observedAt: 1001, fresh: true,
}, 1010);
const staleTelemetry = state.reconcileWallObservation(pending, {
  provider: 'a'.repeat(32), revision: 41, observed: {...desired},
  observedAt: 1001, fresh: false,
}, 1010);
const mismatch = state.reconcileWallObservation(pending, {
  provider: 'a'.repeat(32), revision: 41,
  observed: {...desired, brightness: 32}, observedAt: 1001, fresh: true,
}, 1010);
const reconnected = state.reconcileWallObservation(pending, {
  provider: 'b'.repeat(32), revision: 1, observed: {...desired},
  observedAt: 1001, fresh: true,
}, 1010);
console.log(JSON.stringify({
  acknowledgement, stale, staleTelemetry, mismatch, reconnected,
  pending: {provider: pending.provider, revision: pending.revision, desired: pending.desired},
}));
""")

        self.assertEqual(result["pending"], {
            "provider": "a" * 32,
            "revision": 40,
            "desired": {
                "vibeId": "cozy", "brightness": 96,
                "targetFps": 120, "speedMultiplier": 1.5,
            },
        })
        self.assertEqual(result["acknowledgement"]["state"], "acknowledged")
        self.assertTrue(result["acknowledgement"]["acknowledged"])
        self.assertEqual(result["stale"]["state"], "waiting")
        self.assertFalse(result["stale"]["acknowledged"])
        self.assertEqual(result["staleTelemetry"]["state"], "stale")
        self.assertFalse(result["staleTelemetry"]["acknowledged"])
        self.assertEqual(result["mismatch"]["state"], "mismatch")
        self.assertTrue(result["mismatch"]["retryable"])
        self.assertEqual(result["reconnected"]["state"], "reconnected")
        self.assertTrue(result["reconnected"]["retryable"])

    def test_preset_identity_is_exact_check_binding_and_idempotent_when_unchanged(self) -> None:
        result = _run_state_script("""
const component = {key: 'python:gradient', browser_runtime: {digest: 'runtime-1'}};
const geometry = {strip_count: 33, leds_per_strip: 138, total_leds: 4554};
const beforeSave = state.checkBinding(4, component, geometry, null, 'profile-a', null);
const savedPreset = {
  preset_id: 'browser_look',
  preset_fingerprint: 'a'.repeat(64),
};
const afterSave = state.checkBinding(4, component, geometry, null, 'profile-a', savedPreset);
const unchangedSave = state.checkBinding(4, component, geometry, null, 'profile-a', {...savedPreset});
const overwrittenPreset = {...savedPreset, preset_fingerprint: 'b'.repeat(64)};
const afterOverwrite = state.checkBinding(4, component, geometry, null, 'profile-a', overwrittenPreset);
const checkedBeforeSave = {status: 'pass', binding: beforeSave};
const checkedAfterSave = {status: 'pass', binding: afterSave};
console.log(JSON.stringify({
  beforeSave,
  afterSave,
  staleImmediatelyAfterSave: state.checkAllowsActivation(checkedBeforeSave, afterSave),
  eligibleAfterRerun: state.checkAllowsActivation(checkedAfterSave, afterSave),
  unchangedSaveStaysCurrent: state.sameCheckBinding(afterSave, unchangedSave),
  overwriteIsStale: state.sameCheckBinding(afterSave, afterOverwrite),
  snakeIdentity: state.componentPresetIdentity(savedPreset),
  camelIdentity: state.componentPresetIdentity({presetId: 'browser_look', presetFingerprint: 'a'.repeat(64)}),
}));
""")

        identity = {
            "presetId": "browser_look",
            "presetFingerprint": "a" * 64,
        }
        self.assertIsNone(result["beforeSave"]["presetIdentity"])
        self.assertEqual(result["afterSave"]["checkerVersion"], "browser-checker-v4")
        self.assertEqual(result["afterSave"]["presetIdentity"], identity)
        self.assertFalse(result["staleImmediatelyAfterSave"])
        self.assertTrue(result["eligibleAfterRerun"])
        self.assertTrue(result["unchangedSaveStaysCurrent"])
        self.assertFalse(result["overwriteIsStale"])
        self.assertEqual(result["snakeIdentity"], identity)
        self.assertEqual(result["camelIdentity"], identity)

    def test_application_invalidates_checks_and_snapshots_full_scene_history(self) -> None:
        source = COMPOSER_SOURCE.read_text(encoding="utf-8")

        self.assertIn("draftGeneration: 0", source)
        self.assertIn("state.checkResult = null", source)
        self.assertIn("state.checkerGeneration += 1", source)
        self.assertIn("state.documentRevision += 1", source)
        self.assertIn("originalParams: clone(state.originalParams)", source)
        self.assertIn("selectedPreset: state.selectedPreset", source)
        self.assertIn("componentKey: state.component?.key", source)
        self.assertIn("resetChecker({preserveDocumentRevision: true})", source)
        self.assertIn("invalidateCheckerForPresetIdentityChange", source)
        self.assertIn("adoptImportedPresetIdentity", source)
        self.assertIn("currentCheckAllowsActivation()", source)
        self.assertNotIn("Run Check for this exact draft before activation.", source)
        self.assertIn("await createServerCheck(", source)
        self.assertIn("await submitCheckedIntent(entry.intent, serverCheck)", source)
        self.assertNotIn("Apply or revert the Wall draft before activating this checked scene.", source)
        self.assertIn("refreshGlobalSettings({quiet: true, preserveDraft: true})", source)
        self.assertIn("wallSettings: clone(state.globalSettings.draft)", source)
        self.assertIn("frameBudgetMs = 1000 / targetFps", source)
        self.assertIn("ComposerState.advisoryRenderStatus(p95, frameBudgetMs)", source)
        self.assertNotIn("failures.push('render time')", source)
        self.assertIn("serverCheck: null", source)
        self.assertIn("Pending · newest edit queued for a guarded apply.", source)
        self.assertIn("activationIdentitiesMatch(status)", source)
        self.assertIn("warnings: warnings.slice()", source)
        self.assertIn("browser_evidence: clone(state.checkResult)", source)
        self.assertIn("if (editing && key === 'z') return", source)
        self.assertIn("detail.textContent = detail.dataset.defaultDescription", source)

    def test_import_and_keyboard_contracts_are_bounded_and_explicit(self) -> None:
        source = COMPOSER_SOURCE.read_text(encoding="utf-8")

        self.assertIn("file.size > 256 * 1024", source)
        self.assertIn("depth > 16", source)
        self.assertIn("budget.nodes > 4096", source)
        self.assertIn("['__proto__', 'prototype', 'constructor']", source)
        self.assertIn("schema === 'ledgrid.browser-scene'", source)
        self.assertIn("layer?.slot_id !== 'clock_overlay'", source)
        self.assertIn("blend_mode: 'source_over'", source)
        self.assertIn("event.key === 'Home'", source)
        self.assertIn("event.key === 'End'", source)
        self.assertIn("Starting points for the current animation", source)
        self.assertIn("function isAdvancedParameter", source)
        self.assertIn("advancedParameterList", source)
        self.assertIn("browser_capabilities?.managed_identity", source)
        self.assertIn("status.telemetry?.complete", source)
        self.assertIn("check_token: serverCheck.token", source)
        self.assertIn("'Idempotency-Key': serverCheck.idempotencyKey", source)
        self.assertIn("method: 'DELETE'", source)
        self.assertIn("`${state.activation.statusUrl}/rollback`", source)
        self.assertIn("expected_controller_state_revision: status.controller.state_revision_after", source)
        self.assertIn("accepted.request_status_url", source)
        self.assertIn("function pollActivationResourceResult()", source)
        self.assertIn("result.outcome === 'pending'", source)
        self.assertIn("do not infer success", source)
        self.assertIn("Local preview · edits stay on this device until you go Live.", source)
        self.assertIn("async function toggleLiveWall()", source)
        self.assertIn("state.globalSettings.draft.power = true", source)
        self.assertIn("queueImmediateApply({immediate: true, source: 'Go Live'})", source)
        self.assertIn("if (!state.liveWall.enabled)", source)
        self.assertIn("prepareOfflineButton", source)
        self.assertIn("const activationAvailable = state.bootstrap?.capabilities?.server_actions?.activation_available === true", source)
        self.assertIn("activationMode === 'development_canary'", source)
        self.assertIn("physical activation is disabled", source)
        self.assertIn("Physical activation remains disabled for this release", source)
        self.assertIn("Previously active · no longer current", source)
        persistence_calls = "\n".join(
            line for line in source.splitlines()
            if "localStorage" in line or "sessionStorage" in line
        )
        self.assertNotIn("serverCheck", persistence_calls)
        self.assertNotIn("check_token", persistence_calls)

    def test_application_has_a_narrow_composer_module_registration_seam(self) -> None:
        source = COMPOSER_SOURCE.read_text(encoding="utf-8")

        self.assertIn("function createComposerModuleRegistry(", source)
        self.assertIn("const ComposerModules = createComposerModuleRegistry({", source)
        self.assertIn("window.LEDGridComposerModules = ComposerModules", source)
        self.assertIn("state: applicationState", source)
        self.assertIn("events: Object.freeze({", source)
        self.assertIn("dom: Object.freeze(dom)", source)
        self.assertIn("runtime: Object.freeze(runtime)", source)
        self.assertIn("registrations.forEach((installer) => installer(context))", source)
        self.assertIn("if (context) installer(context)", source)
        self.assertIn("ComposerModules.register('core-runtime-cleanup'", source)
        self.assertIn("ComposerModules.initialize()", source)

        html = (ROOT / "web/templates/composer.html").read_text(encoding="utf-8")
        self.assertIn('id="cancelActivationButton"', html)
        self.assertIn('id="rollbackActivationButton"', html)
        self.assertLess(html.index("composer_state.js"), html.index("composer_runtime.js"))

    def test_application_replays_saved_library_selection_with_semantic_urls_only(self) -> None:
        source = COMPOSER_SOURCE.read_text(encoding="utf-8")

        for marker in (
            "LIBRARY_FAVORITES_STORAGE_KEY",
            "LIBRARY_RECENTS_STORAGE_KEY",
            "function rememberLibrarySelection()",
            "function restoreLibrarySelection(selection)",
            "skipLibraryRecent: true",
            "toggleLibraryFavoriteButton",
            "renderLocalLibrary()",
            "function selectCatalogEntry(entry)",
            "CATALOG_INITIAL_RESULT_LIMIT",
            "catalogShowMoreButton",
            "data-catalog-kind",
            "data-catalog-saved",
        ):
            self.assertIn(marker, source)
        self.assertIn("syncComposerUrl({mode: urlMode})", source)
        self.assertNotIn("fetch('/api/v1", source[source.index("function restoreLibrarySelection"):source.index("function parsedComposerUrl")])


if __name__ == "__main__":
    unittest.main()

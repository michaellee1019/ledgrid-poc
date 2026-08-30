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


@unittest.skipUnless(shutil.which("node"), "node unavailable")
class BrowserComposerStateTests(unittest.TestCase):
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
        self.assertIn("the server will issue the authoritative Check", source)
        self.assertNotIn("Apply or revert the Wall draft before activating this checked scene.", source)
        self.assertIn("refreshGlobalSettings({quiet: true, preserveDraft: false})", source)
        self.assertIn("wallSettings: clone(state.globalSettings.draft)", source)
        self.assertIn("frameBudgetMs = 1000 / targetFps", source)
        self.assertIn("ComposerState.advisoryRenderStatus(p95, frameBudgetMs)", source)
        self.assertNotIn("failures.push('render time')", source)
        self.assertIn("serverCheck: null", source)
        self.assertIn("Pending · activation", source)
        self.assertIn("activationIdentitiesMatch(status)", source)
        self.assertIn("warnings: warnings.slice()", source)
        self.assertIn("review ${state.checkResult.warnings.join(', ')}", source)
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
        self.assertIn("Passed with cautions", source)
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


if __name__ == "__main__":
    unittest.main()

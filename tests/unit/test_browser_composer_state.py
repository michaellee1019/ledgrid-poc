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

    def test_check_binding_covers_generation_runtime_digest_and_geometry(self) -> None:
        result = _run_state_script("""
const component = {
  key: 'receiver_native:aurora',
  browser_runtime: {supported: true, asset_digest: 'wasm-123'},
  activation_capability: {
    previewable: true, saveable: true, activation_ready: false,
    reason: 'Managed receiver identity is unavailable.'
  }
};
const first = state.checkBinding(7, component, {strip_count: 33, leds_per_strip: 138, total_leds: 4554});
const next = state.checkBinding(8, component, {strip_count: 33, leds_per_strip: 138, total_leds: 4554});
console.log(JSON.stringify({
  first,
  same: state.sameCheckBinding(first, {...first}),
  stale: state.sameCheckBinding(first, next),
  capability: state.capability(component),
}));
""")

        self.assertEqual(result["first"]["draftGeneration"], 7)
        self.assertEqual(result["first"]["runtimeDigest"], "wasm-123")
        self.assertEqual(result["first"]["geometry"]["totalLeds"], 4554)
        self.assertTrue(result["same"])
        self.assertFalse(result["stale"])
        self.assertFalse(result["capability"]["activationReady"])
        self.assertIn("identity", result["capability"]["reason"])

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
        self.assertIn("currentCheckIsPassing()", source)
        self.assertIn("Activation requires a passing Check", source)
        self.assertIn("detail.textContent = detail.dataset.defaultDescription", source)

    def test_import_and_keyboard_contracts_are_bounded_and_explicit(self) -> None:
        source = COMPOSER_SOURCE.read_text(encoding="utf-8")

        self.assertIn("file.size > 512 * 1024", source)
        self.assertIn("depth > 12", source)
        self.assertIn("budget.nodes > 10000", source)
        self.assertIn("['__proto__', 'prototype', 'constructor']", source)
        self.assertIn("payload.version !== 2", source)
        self.assertIn("layer?.slot_id !== 'clock_overlay'", source)
        self.assertIn("blend_mode: 'source-over'", source)
        self.assertIn("event.key === 'Home'", source)
        self.assertIn("event.key === 'End'", source)
        self.assertIn("Starting points for the current animation", source)


if __name__ == "__main__":
    unittest.main()

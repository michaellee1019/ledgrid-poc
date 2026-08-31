"""Focused global-observation contracts for the browser Composer."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
STATE_SOURCE = ROOT / "web/static/js/composer_state.js"
COMPOSER_SOURCE = ROOT / "web/static/js/composer.js"


def _state_script(body: str) -> dict:
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("node is required for Composer global-state tests")
    source = json.dumps(str(STATE_SOURCE))
    result = subprocess.run(
        [node, "-e", f"const fs=require('fs'); const vm=require('vm'); vm.runInThisContext(fs.readFileSync({source}, 'utf8')); const state=globalThis.LEDGridComposerState; {body}"],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


@unittest.skipUnless(shutil.which("node"), "node is required for Composer global-state tests")
class BrowserComposerGlobalReconciliationTests(unittest.TestCase):
    def test_fresh_qualified_observation_owns_cached_globals_and_reconnect(self) -> None:
        result = _state_script("""
const observed = {power:false, vibeId:'neutral', brightness:71, targetFps:47, speedMultiplier:1.4, plantModifiers:{version:1,active:[],strengths:{}}};
const stale = {power:true, vibeId:'vivid', brightness:220, targetFps:120, speedMultiplier:2.7, plantModifiers:{version:1,active:['shadow'],strengths:{shadow:.5}}};
const edited = {...observed, speedMultiplier:1.8};
console.log(JSON.stringify({
  qualified: state.qualifiedWallObservation({sessionId:'a'.repeat(32), revision:9, fresh:true}),
  staleTelemetry: state.qualifiedWallObservation({sessionId:'a'.repeat(32), revision:9, fresh:false}),
  missingRevision: state.qualifiedWallObservation({sessionId:'a'.repeat(32), revision:null, fresh:true}),
  freshTimestamp: state.wallObservationTimestampIsFresh(995000, 1000000),
  staleTimestamp: state.wallObservationTimestampIsFresh(984999, 1000000),
  futureTimestamp: state.wallObservationTimestampIsFresh(1005001, 1000000),
  missingTimestamp: state.wallObservationTimestampIsFresh(null, 1000000),
  first: state.draftFromWallObservation(observed, stale, [], {reset:true}),
  sameSession: state.draftFromWallObservation({...observed, brightness:72}, edited, ['speedMultiplier']),
  reconnect: state.draftFromWallObservation({...observed, brightness:72}, edited, ['speedMultiplier'], {reset:true}),
}));
""")
        self.assertTrue(result["qualified"])
        self.assertFalse(result["staleTelemetry"])
        self.assertFalse(result["missingRevision"])
        self.assertTrue(result["freshTimestamp"])
        self.assertFalse(result["staleTimestamp"])
        self.assertFalse(result["futureTimestamp"])
        self.assertFalse(result["missingTimestamp"])
        self.assertEqual(result["first"], {
            "power": False, "vibeId": "neutral", "brightness": 71,
            "targetFps": 47, "speedMultiplier": 1.4,
            "plantModifiers": {"version": 1, "active": [], "strengths": {}},
        })
        self.assertEqual(result["sameSession"]["brightness"], 72)
        self.assertEqual(result["sameSession"]["speedMultiplier"], 1.8)
        self.assertEqual(result["reconnect"]["speedMultiplier"], 1.4)

    def test_app_never_restores_persisted_globals_and_blocks_unqualified_activation(self) -> None:
        source = COMPOSER_SOURCE.read_text(encoding="utf-8")
        init = source[source.index("function initializeGlobalSettings()"):source.index("function globalChangeList()")]
        refresh = source[source.index("async function refreshGlobalSettings"):source.index("function renderOperationsStatus")]
        self.assertNotIn("loadStoredGlobalDraft", source)
        self.assertIn("localStorage.removeItem(`${STORAGE_PREFIX}.global-draft`)", source)
        self.assertIn("sessionEdits: new Set()", source)
        self.assertIn("qualifiedWallObservation", refresh)
        self.assertIn("wallObservationTimestampIsFresh(observedStatusTimeMs(payload))", refresh)
        self.assertNotIn("payload?.telemetry?.fresh", refresh)
        self.assertIn("draftFromWallObservation", refresh)
        self.assertIn("state.globalSettings.sessionEdits.clear();", refresh)
        self.assertIn("state.globalSettings.observationQualified = false;", init)
        activation = source[source.index("function activationBlockReason()"):source.index("function renderImmediateApplyStatus")]
        self.assertIn("Wait for a fresh revision-qualified controller observation.", activation)
        controls = source[source.index("function renderLiveWallControls()"):source.index("function leaveLiveWall", source.index("function renderLiveWallControls()"))]
        self.assertIn("!live.enabled && Boolean(blockReason)", controls)
        self.assertIn("state.globalSettings.sessionEdits.add('power')", source)

    def test_preset_and_saved_record_paths_do_not_own_global_settings(self) -> None:
        source = COMPOSER_SOURCE.read_text(encoding="utf-8")
        preset = source[source.index("function applyPreset("):source.index("function updateComponentCopy", source.index("function applyPreset("))]
        reopen = source[source.index("async function reopenSavedRecord()"):source.index("async function updateSavedRecord()")]
        self.assertNotIn("globalSettings", preset)
        self.assertNotIn("globalSettings", reopen)


if __name__ == "__main__":
    unittest.main()

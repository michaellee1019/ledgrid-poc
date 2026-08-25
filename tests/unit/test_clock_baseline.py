"""Deterministic acceptance tests for the Phase 1 Clock baseline tool."""

import copy
from pathlib import Path
import unittest

import numpy as np

from tools.benchmarks.clock_baseline import (
    SCENARIOS,
    contiguous_ranges,
    evaluate_acceptance,
    measure_scenario,
    run_baseline,
)


class _DeterministicTimer:
    def __init__(self, step_ns=125_000):
        self.value = 0
        self.step_ns = step_ns

    def __call__(self):
        current = self.value
        self.value += self.step_ns
        return current


class ClockBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_baseline(
            duration_seconds=2.0,
            timer_ns=_DeterministicTimer(),
        )
        cls.scenarios = {
            item["scenario_id"]: item for item in cls.report["scenarios"]
        }

    def test_contiguous_ranges_cover_fragmentation_and_empty_masks(self):
        self.assertEqual(contiguous_ranges(np.zeros(8, dtype=bool)), ())
        self.assertEqual(
            contiguous_ranges(np.array([1, 1, 0, 1, 0, 0, 1, 1], dtype=bool)),
            ((0, 2), (3, 4), (6, 8)),
        )

    def test_normal_and_animated_cadences_are_not_multiplied_by_manager_calls(self):
        normal = self.scenarios["normal"]
        animated = self.scenarios["animated"]

        self.assertEqual(normal["manager_calls"], 400)
        self.assertEqual(normal["changed_frames"], 2)
        self.assertEqual(normal["changed_ratio"], 0.005)
        self.assertEqual(animated["manager_calls"], 400)
        self.assertEqual(animated["changed_frames"], 24)
        self.assertEqual(animated["changed_ratio"], 0.06)
        for item in (normal, animated):
            self.assertEqual(item["cached_byte_mismatches"], 0)
            self.assertEqual(item["changed_without_pixel_delta"], 0)
            self.assertEqual(item["declared_dirty_metadata_frames"], 0)

    def test_latency_percentiles_use_every_manager_call_and_changed_subset(self):
        for item in self.scenarios.values():
            for population in ("all_calls", "changed_calls", "cached_calls"):
                summary = item["latency_ms"][population]
                self.assertEqual(summary["p50"], 0.125)
                self.assertEqual(summary["p95"], 0.125)
                self.assertEqual(summary["p99"], 0.125)
                self.assertEqual(summary["max"], 0.125)

    def test_full_frame_and_sparse_potential_are_reported_without_protocol_claims(self):
        facts = self.report["full_frame_facts"]
        self.assertEqual(facts["rgb_bytes"], 33 * 138 * 3)
        self.assertEqual(facts["rgb_bytes_per_second_at_manager_fps"], 33 * 138 * 3 * 200)
        self.assertIn("headers", facts["excludes"])
        self.assertEqual(
            self.scenarios["normal"]["current_full_rgb_payload_bytes_per_second"],
            33 * 138 * 3,
        )
        self.assertLess(
            self.scenarios["normal"]["derived_payload_ratio_on_changed_frames"],
            0.10,
        )

    def test_actual_preview_path_characterizes_static_and_animated_clock(self):
        preview = self.report["preview"]
        self.assertEqual(preview["capture_seconds"], [0.0, 0.5, 1.0, 2.0, 3.5, 5.5, 8.0, 12.0])
        self.assertEqual(preview["simulation_fps"], 30)
        self.assertTrue(preview["default"]["static"])
        self.assertEqual(preview["default"]["authored_frames"], 1)
        self.assertFalse(preview["animated"]["static"])
        self.assertEqual(preview["animated"]["authored_frames"], 8)
        self.assertEqual(preview["animated"]["encoded_loop_duration_ms"], 4000)

    def test_committed_report_records_metrics_machine_and_hardware_boundary(self):
        report_path = Path(__file__).resolve().parents[2] / "docs" / "clock-phase1-baseline.md"
        committed = report_path.read_text(encoding="utf-8")

        self.assertIn("| normal | 10/2000 | 0.500% | 1.0 Hz |", committed)
        self.assertIn("| animated | 120/2000 | 6.000% | 12.0 Hz |", committed)
        self.assertIn("One complete 33 x 138 RGB frame is **13,662 bytes**", committed)
        self.assertIn("full-scene diff is only a proxy", committed)
        self.assertIn("not Raspberry Pi or ESP32 evidence", committed)
        self.assertIn("Clean deployment receipt", committed)

    def test_acceptance_passes_and_reports_actionable_cadence_failure(self):
        self.assertEqual(evaluate_acceptance(self.report), [])
        broken = copy.deepcopy(self.report)
        broken["scenarios"][0]["changed_frames"] = 400

        failures = evaluate_acceptance(broken)

        self.assertTrue(any("expected 2 semantic/source ticks" in item for item in failures))

    def test_measurement_rejects_non_positive_operating_envelope(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            measure_scenario(SCENARIOS[0], manager_fps=0)
        with self.assertRaisesRegex(ValueError, "positive"):
            measure_scenario(SCENARIOS[0], duration_seconds=0)


if __name__ == "__main__":
    unittest.main()

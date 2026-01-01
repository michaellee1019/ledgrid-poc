"""Fluid tank regression tests driven by the shared simulation harness."""

import unittest

from tools.diagnostics.fluid_tank_simulation import SimulationConfig, run_simulation


class FluidTankSimulationTests(unittest.TestCase):
    def test_fill_curve_and_drain_cycle(self):
        samples = run_simulation(SimulationConfig(duration_s=130.0, sample_every_s=2.0))
        fill_before_60 = [s['stats']['fill_ratio'] for s in samples if s['timestamp'] <= 60.0]
        self.assertTrue(fill_before_60)
        self.assertGreaterEqual(max(fill_before_60), 0.85)
        self.assertTrue(any(s['stats'].get('hole_active') for s in samples), "hole never opened")
        drained_fill = [s['stats']['fill_ratio'] for s in samples if s['timestamp'] >= 90.0]
        self.assertTrue(drained_fill, "no samples captured after 90s")
        self.assertLessEqual(min(drained_fill), 0.2, "tank never drained after puncture")

    def test_stats_schema_and_scene_features(self):
        samples = run_simulation(SimulationConfig(duration_s=110.0, sample_every_s=1.0))
        for sample in samples:
            self.assertEqual(sample['current_animation'], "Fluid Tank")
            self.assertIn('stats', sample)
            self.assertIsInstance(sample['stats'], dict)
        self.assertTrue(
            any(s['stats'].get('max_bubble_rise', 0.0) > 5.0 for s in samples),
            "bubbles never rose",
        )
        self.assertTrue(
            any(s['stats'].get('last_spray_time', 0.0) > 0.0 for s in samples),
            "no spray events recorded",
        )


if __name__ == "__main__":
    unittest.main()

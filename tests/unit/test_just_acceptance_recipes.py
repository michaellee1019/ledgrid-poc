"""Regression coverage for the maintained non-mutating benchmark recipes."""

from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]


class JustAcceptanceRecipeTests(unittest.TestCase):
    def dry_run(self, *args):
        result = subprocess.run(
            ["just", "--dry-run", *args], cwd=ROOT, check=True,
            capture_output=True, text=True,
        )
        return result.stdout + result.stderr

    def test_local_readiness_gate_runs_the_installed_scene_render_benchmark(self):
        command = self.dry_run("preflight")
        self.assertIn("tests/unit/test_all_composer_animations_live.py", command)
        self.assertIn("tests/unit/test_browser_python_catalog_matrix.py", command)
        self.assertIn("python tools/benchmarks/animation_render.py --frames 100 --stress --check", command)

    def test_output_rate_observation_accepts_a_sample_duration(self):
        command = self.dry_run("output-rate-observation", "10")
        self.assertIn("python tools/benchmarks/output_rate_sweep.py", command)
        self.assertIn('--seconds "10"', command)


if __name__ == "__main__":
    unittest.main()

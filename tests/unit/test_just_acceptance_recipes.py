"""Regression coverage for documented Just hardware-acceptance invocations."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class JustAcceptanceRecipeTests(unittest.TestCase):
    def run_with_fake_uv(self, *args: str) -> list[str]:
        """Execute Just normally, intercepting uv before any Python/hardware code."""
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_uv = Path(temp_dir) / "uv"
            fake_uv.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@"\n', encoding="utf-8")
            fake_uv.chmod(0o755)
            env = dict(os.environ, PATH=f"{temp_dir}{os.pathsep}{os.environ['PATH']}")
            result = subprocess.run(
                ["just", *args], cwd=ROOT, env=env, check=True,
                capture_output=True, text=True,
            )
        return result.stdout.splitlines()

    def test_documented_named_streamed_wall_invocation_is_normalized(self):
        argv = self.run_with_fake_uv(
            "receiver-streamed-wall-acceptance",
            "duration=60",
            "min_fps=150",
            "target_fps=160",
        )
        self.assertEqual(argv[-18:], [
            "python", "tools/benchmarks/receiver_acceptance.py",
            "--device", "0", "--device", "1", "--device", "2", "--device", "3",
            "--duration", "60", "--min-displayed-fps", "150",
            "--target-fps", "160", "--animation", "rainbow",
        ])

    def test_degraded_spi1_recipe_is_separate_explicit_and_full_wall(self):
        argv = self.run_with_fake_uv(
            "receiver-streamed-wall-acceptance-degraded-spi1",
            "duration=60",
            "min_fps=150",
            "target_fps=160",
        )
        self.assertEqual(argv[-19:], [
            "python", "tools/benchmarks/receiver_acceptance.py",
            "--device", "0", "--device", "1", "--device", "2", "--device", "3",
            "--allow-degraded-spi1-return-path",
            "--duration", "60", "--min-displayed-fps", "150",
            "--target-fps", "160", "--animation", "rainbow",
        ])

    def test_degraded_status_recipe_is_separate_and_explicit(self):
        argv = self.run_with_fake_uv("receiver-phase3a-status-degraded-spi1")
        self.assertEqual(argv[-4:], [
            "python", "tools/benchmarks/receiver_acceptance.py",
            "--phase3a-status-only", "--allow-degraded-spi1-return-path",
        ])

    def test_documented_named_live_sweep_invocation_is_normalized(self):
        argv = self.run_with_fake_uv("live-animation-sweep", "seconds=2")
        self.assertEqual(
            argv[-4:],
            ["python", "tools/benchmarks/live_animation_sweep.py", "--seconds", "2"],
        )

    def test_degraded_live_sweep_is_separate_and_explicit(self):
        argv = self.run_with_fake_uv(
            "live-animation-sweep-degraded-spi1", "seconds=2"
        )
        self.assertEqual(argv[-5:], [
            "python", "tools/benchmarks/live_animation_sweep.py",
            "--allow-degraded-spi1-return-path", "--seconds", "2",
        ])

    def test_defaults_and_positional_arguments_remain_supported(self):
        default_receiver = self.run_with_fake_uv("receiver-acceptance")
        self.assertEqual(default_receiver[-10:-2], [
            "--device", "0", "--duration", "60", "--min-displayed-fps", "150",
            "--target-fps", "160",
        ])

        default_stream = self.run_with_fake_uv("receiver-streamed-wall-acceptance")
        self.assertEqual(default_stream[-8:-2], [
            "--duration", "60", "--min-displayed-fps", "150", "--target-fps", "160",
        ])

        default_degraded = self.run_with_fake_uv(
            "receiver-streamed-wall-acceptance-degraded-spi1"
        )
        self.assertEqual(default_degraded[-8:-2], [
            "--duration", "60", "--min-displayed-fps", "150", "--target-fps", "160",
        ])

        positional_stream = self.run_with_fake_uv(
            "receiver-streamed-wall-acceptance", "30", "170", "190"
        )
        self.assertEqual(positional_stream[-8:-2], [
            "--duration", "30", "--min-displayed-fps", "170", "--target-fps", "190",
        ])

        default_sweep = self.run_with_fake_uv("live-animation-sweep")
        positional_sweep = self.run_with_fake_uv("live-animation-sweep", "4")
        self.assertEqual(default_sweep[-2:], ["--seconds", "2"])
        self.assertEqual(positional_sweep[-2:], ["--seconds", "4"])

    def test_other_named_acceptance_recipes_are_normalized_consistently(self):
        receiver = self.run_with_fake_uv(
            "receiver-acceptance", "device=2", "duration=30",
            "min_fps=170", "target_fps=190",
        )
        self.assertEqual(receiver[-10:-2], [
            "--device", "2", "--duration", "30", "--min-displayed-fps", "170",
            "--target-fps", "190",
        ])

        rates = self.run_with_fake_uv(
            "output-rate-sweep", "seconds=10", "rates=120,160,180"
        )
        self.assertEqual(
            rates[-6:],
            ["python", "tools/benchmarks/output_rate_sweep.py", "--seconds", "10",
             "--rates", "120,160,180"],
        )


if __name__ == "__main__":
    unittest.main()

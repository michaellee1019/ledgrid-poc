"""Regression coverage for documented Just hardware-acceptance invocations."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
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
        self.assertEqual(argv[-20:], [
            "python", "tools/benchmarks/receiver_acceptance.py",
            "--device", "0", "--device", "1", "--device", "2", "--device", "3",
            "--device", "4",
            "--duration", "60", "--min-displayed-fps", "150",
            "--target-fps", "160", "--animation", "rainbow",
        ])

    def test_native_h2_and_h4_recipes_keep_explicit_real_soak_defaults(self):
        for recipe, gate in (
            ("receiver-native-h2-evidence", "H2"),
            ("receiver-native-h4-default-soak", "H4-default"),
            ("receiver-native-h4-maximum-soak", "H4-maximum"),
        ):
            with self.subTest(recipe=recipe):
                argv = self.run_with_fake_uv(recipe)
                self.assertEqual(argv[-11:], [
                    "python",
                    "tools/benchmarks/receiver_native_physical_acceptance.py",
                    "aurora_curtains_native",
                    "--gate", gate,
                    "--target", "ledgridwall.local",
                    "--duration", "1800",
                    "--sample-interval", "5",
                ])

    def test_native_physical_recipe_accepts_short_named_diagnostic_duration(self):
        argv = self.run_with_fake_uv(
            "receiver-native-h2-evidence",
            "selector=aurora_curtains_native",
            "duration=0.2",
            "sample_interval=0.05",
            "target=wall.test",
        )
        self.assertEqual(argv[-11:], [
            "python", "tools/benchmarks/receiver_native_physical_acceptance.py",
            "aurora_curtains_native", "--gate", "H2",
            "--target", "wall.test", "--duration", "0.2",
            "--sample-interval", "0.05",
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

    def test_phase3b_canary_recipe_requires_explicit_readable_binding(self):
        argv = self.run_with_fake_uv(
            "receiver-phase3b-physical-canary", "0", "1", "1", "7"
        )
        self.assertEqual(argv[-10:], [
            "python", "tools/benchmarks/phase3b_single_receiver_canary.py",
            "--bus", "0", "--device", "1", "--logical-id", "1",
            "--disconnect-seconds", "7",
        ])

    def test_phase3b_degraded_showcase_requires_explicit_restoration_and_confirmation(self):
        argv = self.run_with_fake_uv(
            "receiver-phase3b-degraded-showcase",
            "state.json", "frame.npy", "challenge.json", "response.json", "20",
        )
        self.assertEqual(argv[-12:], [
            "python", "tools/benchmarks/phase3b_degraded_showcase.py",
            "--desired-display-state", "state.json",
            "--restore-frame-npy", "frame.npy",
            "--confirmation-challenge", "challenge.json",
            "--confirmation-response", "response.json",
            "--duration", "20",
        ])

    def test_physical_canary_scripts_start_directly_outside_repository(self):
        scripts = (
            "phase3a_single_receiver_canary.py",
            "phase3b_single_receiver_canary.py",
            "phase3b_degraded_showcase.py",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "spidev.py").write_text(
                "class SpiDev:\n    pass\n", encoding="utf-8"
            )
            env = dict(os.environ, PYTHONPATH=temp_dir)
            for script in scripts:
                with self.subTest(script=script):
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(ROOT / "tools" / "benchmarks" / script),
                            "--help",
                        ],
                        cwd=temp_path,
                        env=env,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("usage:", result.stdout)

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

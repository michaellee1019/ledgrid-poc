"""Deterministic coverage for opt-in plant-aware Fireworks behavior."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from animation.plugins.fireworks import FireworksAnimation, Spark


class _Controller:
    strip_count = 8
    leds_per_strip = 24
    total_leds = strip_count * leds_per_strip


class PlantAwareFireworksTests(unittest.TestCase):
    def _mask_files(self, root: Path, foliage=(), globes=()):
        foliage_path = root / "foliage.json"
        globe_path = root / "globes.json"
        foliage_path.write_text(
            json.dumps({"covered_indices": sorted(foliage)}), encoding="utf-8"
        )
        globe_path.write_text(
            json.dumps({"globe_indices": sorted(globes), "region_count": 1}),
            encoding="utf-8",
        )
        return foliage_path, globe_path

    @staticmethod
    def _index(x, canvas_y):
        return x * _Controller.leds_per_strip + (_Controller.leds_per_strip - 1 - canvas_y)

    def test_disabled_mode_preserves_original_frames_and_simulation(self):
        config = {"random_seed": 91, "star_density": 0.02}
        ordinary = FireworksAnimation(_Controller(), config)
        disabled = FireworksAnimation(_Controller(), {**config, "plant_aware": False})

        for frame_count in range(90):
            elapsed = frame_count / 60.0
            np.testing.assert_array_equal(
                ordinary.generate_frame(elapsed, frame_count),
                disabled.generate_frame(elapsed, frame_count),
            )
            self.assertEqual(ordinary._rockets, disabled._rockets)
            self.assertEqual(ordinary._sparks, disabled._sparks)

    def test_launches_use_visible_corridor_and_safe_burst_position(self):
        # Six occluded strips leave a narrow pair of fully visible launch lanes.
        foliage = {
            self._index(x, y)
            for x in range(6)
            for y in range(_Controller.leds_per_strip)
        }
        with tempfile.TemporaryDirectory() as directory:
            foliage_path, globe_path = self._mask_files(Path(directory), foliage)
            animation = FireworksAnimation(
                _Controller(),
                {
                    "random_seed": 7,
                    "plant_aware": True,
                    "plant_clearance": 0,
                    "plant_mask_path": str(foliage_path),
                    "plant_globe_mask_path": str(globe_path),
                    "star_density": 0.0,
                },
            )

            animation.generate_frame(0.0, 0)

            self.assertEqual(len(animation._rockets), 1)
            rocket = animation._rockets[0]
            column = int(round(rocket.x * (_Controller.strip_count - 1)))
            target_row = int(round(rocket.target_y * (_Controller.leds_per_strip - 1)))
            self.assertGreaterEqual(column, 6)
            self.assertFalse(animation._plant_clearance[target_row, column])
            self.assertEqual(rocket.vx, 0.0)

    def test_sparks_stop_at_plants_and_light_distinct_silhouettes(self):
        foliage_xy = (2, 10)
        globe_xy = (5, 12)
        with tempfile.TemporaryDirectory() as directory:
            foliage_path, globe_path = self._mask_files(
                Path(directory),
                foliage={self._index(*foliage_xy)},
                globes={self._index(*globe_xy)},
            )
            animation = FireworksAnimation(
                _Controller(),
                {
                    "random_seed": 4,
                    "plant_aware": True,
                    "plant_clearance": 1,
                    "plant_mask_path": str(foliage_path),
                    "plant_globe_mask_path": str(globe_path),
                    "launch_rate": 0.05,
                    "star_density": 0.0,
                    "background_level": 0.0,
                },
            )
            animation._refresh_plant_geometry()
            animation._launch_accumulator = 0.0
            animation._sparks = [
                Spark(
                    x / (_Controller.strip_count - 1),
                    y / (_Controller.leds_per_strip - 1),
                    0.0, 0.0, 0.0, 1.0, (255.0, 255.0, 255.0),
                )
                for x, y in (foliage_xy, globe_xy)
            ]

            animation._update_sparks(0.0, 0.0)
            frame = animation.generate_frame(0.0, 0)

            self.assertEqual(animation._sparks, [])
            self.assertEqual(animation.get_runtime_stats()["plant_hits"], 2)
            foliage_pixel = frame[self._index(*foliage_xy)]
            globe_pixel = frame[self._index(*globe_xy)]
            self.assertGreater(int(foliage_pixel[1]), int(foliage_pixel[0]))
            self.assertGreater(int(globe_pixel[0]), int(globe_pixel[1]))
            self.assertFalse(np.array_equal(foliage_pixel, globe_pixel))


if __name__ == "__main__":
    unittest.main()

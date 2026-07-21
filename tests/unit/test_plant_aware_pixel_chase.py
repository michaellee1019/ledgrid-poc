"""Deterministic coverage for opt-in plant-aware Pixel Chase routing."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from animation.plugins.pixel_chase import PixelChaseAnimation


class _Controller:
    strip_count = 2
    leds_per_strip = 5
    total_leds = strip_count * leds_per_strip


class PlantAwarePixelChaseTests(unittest.TestCase):
    def _animation(self, root: Path, *, enabled=True, clearance=0):
        foliage = root / "foliage.json"
        globes = root / "globes.json"
        foliage.write_text(json.dumps({"covered_indices": [3]}), encoding="utf-8")
        globes.write_text(
            json.dumps({"globe_indices": [1], "region_count": 1}), encoding="utf-8"
        )
        return PixelChaseAnimation(_Controller(), {
            "pixels_per_second": 10.0,
            "red": 200,
            "green": 100,
            "blue": 50,
            "plant_aware": enabled,
            "plant_clearance": clearance,
            "plant_mask_path": str(foliage),
            "plant_globe_mask_path": str(globes),
        })

    def test_disabled_mode_preserves_exact_physical_order_and_color(self):
        with tempfile.TemporaryDirectory() as directory:
            animation = self._animation(Path(directory), enabled=False)
            self.assertEqual(animation._path.tolist(), [4, 3, 2, 1, 0, 9, 8, 7, 6, 5])
            frame = animation.generate_frame(0.1, 1).pixels
            self.assertEqual(np.flatnonzero(np.any(frame, axis=1)).tolist(), [3])
            np.testing.assert_array_equal(frame[3], (200, 100, 50))

    def test_enabled_mode_visits_visible_pixels_before_semantic_masks(self):
        with tempfile.TemporaryDirectory() as directory:
            animation = self._animation(Path(directory))

            # Every LED remains diagnosable. Within each pass, the original
            # top-to-bottom physical wiring order is retained.
            self.assertEqual(animation._path.tolist(), [4, 2, 0, 9, 8, 7, 6, 5, 3, 1])
            self.assertEqual(sorted(animation._path.tolist()), list(range(10)))

            foliage = animation.generate_frame(0.8, 8).pixels
            np.testing.assert_array_equal(foliage[3], (24, 255, 72))
            self.assertEqual(animation.get_runtime_stats()["plant_layer"], "foliage")

            globe = animation.generate_frame(0.9, 9).pixels
            np.testing.assert_array_equal(globe[1], (80, 180, 255))
            self.assertEqual(animation.get_runtime_stats()["plant_layer"], "globe")

    def test_clearance_ring_is_deferred_but_not_recolored_or_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            animation = self._animation(Path(directory), clearance=1)
            masks = animation.get_plant_masks()
            kinds = dict(zip(animation._path.tolist(), animation._path_kind.tolist()))

            ring = np.flatnonzero(
                masks.clearance_flat & ~masks.foliage_flat & ~masks.globes_flat
            ).tolist()
            self.assertTrue(ring)
            self.assertTrue(all(kinds[index] == animation._CLEARANCE for index in ring))
            self.assertEqual(sorted(animation._path.tolist()), list(range(10)))

            ring_index = ring[0]
            step = animation._path.tolist().index(ring_index)
            frame = animation.generate_frame(step / 10.0, step).pixels
            np.testing.assert_array_equal(frame[ring_index], (200, 100, 50))

    def test_runtime_toggle_rebuilds_route(self):
        with tempfile.TemporaryDirectory() as directory:
            animation = self._animation(Path(directory), enabled=False)
            animation.generate_frame(0.0, 0)
            animation.update_parameters({"plant_aware": True})

            self.assertEqual(animation._path[-2:].tolist(), [3, 1])
            self.assertEqual(animation.get_runtime_stats(), {
                "pixel_index": None,
                "plant_aware": True,
            })


if __name__ == "__main__":
    unittest.main()

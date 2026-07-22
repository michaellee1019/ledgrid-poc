"""Deterministic coverage for opt-in plant-aware Flame Burst behavior."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from animation.plugins.flame_burst import FlameBurstAnimation


class _Controller:
    strip_count = 8
    leds_per_strip = 12
    total_leds = strip_count * leds_per_strip


class PlantAwareFlameBurstTests(unittest.TestCase):
    @staticmethod
    def _index(x, y):
        return x * _Controller.leds_per_strip + y

    def _masks(self, root, foliage=(), globes=()):
        foliage_path = root / 'foliage.json'
        globe_path = root / 'globes.json'
        foliage_path.write_text(
            json.dumps({'covered_indices': sorted(foliage)}), encoding='utf-8'
        )
        globe_path.write_text(
            json.dumps({'globe_indices': sorted(globes), 'region_count': 1}),
            encoding='utf-8',
        )
        return foliage_path, globe_path

    def test_disabled_mode_is_exactly_the_baseline_renderer(self):
        config = {
            'speed': 1.3, 'burst_rate': 0.7, 'visible_leds': 12,
            'center_offset_x': 0.5, 'center_offset_y': -1.0,
        }
        ordinary = FlameBurstAnimation(_Controller(), config)
        disabled = FlameBurstAnimation(_Controller(), {**config, 'plant_aware': False})

        for frame_count in (0, 7, 31, 89):
            elapsed = frame_count / 60.0
            np.testing.assert_array_equal(
                ordinary.generate_frame(elapsed, frame_count),
                disabled.generate_frame(elapsed, frame_count),
            )

    def test_ignition_moves_to_safe_pixel_and_wave_routes_around_wall(self):
        # A wall between the ignition and target has one opening at the bottom.
        wall = {self._index(3, y) for y in range(1, _Controller.leds_per_strip)}
        center_block = {self._index(x, y) for x in (0, 1, 2) for y in (5, 6)}
        foliage = wall | center_block
        with tempfile.TemporaryDirectory() as directory:
            foliage_path, globe_path = self._masks(Path(directory), foliage)
            animation = FlameBurstAnimation(_Controller(), {
                'plant_aware': True,
                'plant_clearance': 0,
                'plant_mask_path': str(foliage_path),
                'plant_globe_mask_path': str(globe_path),
                'visible_leds': 12,
                'center_offset_x': -2.5,
            })

            animation.generate_frame(0.4, 24)
            origin = animation._plant_origin
            self.assertIsNotNone(origin)
            self.assertNotIn(self._index(*origin), foliage)

            target_index = self._index(5, 6)
            direct = np.hypot(5 - origin[0], 6 - origin[1])
            route = animation._plant_route_distance[target_index]
            self.assertGreater(route * animation._plant_route_scale, direct + 2.0)

    def test_foliage_and_globes_are_distinct_lit_silhouettes(self):
        foliage_index = self._index(2, 4)
        globe_index = self._index(6, 8)
        with tempfile.TemporaryDirectory() as directory:
            foliage_path, globe_path = self._masks(
                Path(directory), {foliage_index}, {globe_index}
            )
            animation = FlameBurstAnimation(_Controller(), {
                'plant_aware': True,
                'plant_clearance': 0,
                'plant_mask_path': str(foliage_path),
                'plant_globe_mask_path': str(globe_path),
                'visible_leds': 12,
            })
            frame = animation.generate_frame(0.5, 30)

            foliage = frame[foliage_index]
            globe = frame[globe_index]
            self.assertGreater(int(foliage[1]), int(foliage[0]))
            self.assertGreater(int(globe[0]), int(globe[1]))
            self.assertFalse(np.array_equal(foliage, globe))
            stats = animation.get_runtime_stats()
            self.assertTrue(stats['plant_aware'])
            self.assertEqual(stats['plant_foliage_pixels'], 1)
            self.assertEqual(stats['plant_globe_pixels'], 1)


if __name__ == '__main__':
    unittest.main()

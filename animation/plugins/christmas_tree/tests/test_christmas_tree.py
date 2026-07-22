import unittest

import numpy as np

from animation.plugins.christmas_tree import ChristmasTreeAnimation


class _Controller:
    strip_count = 32
    leds_per_strip = 140
    total_leds = strip_count * leds_per_strip


class ChristmasTreeAnimationTests(unittest.TestCase):
    def test_scene_uses_wall_orientation_and_anchors_tree_at_bottom(self):
        animation = ChristmasTreeAnimation(_Controller(), {
            "brightness": 1.0,
            "snowfall_density": 0.0,
            "scene_style": "midnight",
            "show_presents": False,
        })

        frame = animation.generate_frame(1.0, 0)
        physical = frame.reshape(_Controller.strip_count, _Controller.leds_per_strip, 3)

        self.assertIsInstance(frame, np.ndarray)
        self.assertEqual(frame.shape, (_Controller.total_leds, 3))
        self.assertEqual(frame.dtype, np.uint8)
        self.assertTrue(frame.flags.c_contiguous)
        # Physical LED zero is the visual bottom and should contain the snowbank.
        self.assertGreater(float(physical[:, 0].mean()), float(physical[:, -1].mean()) * 5)
        self.assertEqual(max(y for _, y in animation._trunk_pixels), animation._snow_contact_y)

    def test_foliage_is_wider_near_the_ground_than_at_the_crown(self):
        animation = ChristmasTreeAnimation(_Controller())
        animation._build_static_elements()
        rows = {}
        for x, y, _ in animation._tree_pixels:
            rows.setdefault(y, []).append(x)
        ordered = sorted(rows)
        upper = np.mean([len(rows[y]) for y in ordered[: max(2, len(ordered) // 4)]])
        lower = np.mean([len(rows[y]) for y in ordered[-max(2, len(ordered) // 4):]])
        self.assertGreater(lower, upper * 2)

    def test_snow_advances_downward_in_logical_scene_coordinates(self):
        animation = ChristmasTreeAnimation(_Controller(), {"snowfall_density": 0.1})
        animation._build_static_elements()
        animation.snowflakes = [{"x": 5.0, "y": 10.0, "speed": 4.0, "drift": 0.0, "phase": 0.0}]

        animation._update_snowflakes(0.25, 0.0)

        self.assertGreater(animation.snowflakes[0]["y"], 10.0)
        self.assertLess(animation.snowflakes[0]["y"], animation._snow_contact_y)

    def test_scene_and_palette_options_produce_distinct_frames(self):
        fingerprints = set()
        for scene, palette in (
            ("midnight", "classic"),
            ("twilight", "candy"),
            ("aurora", "frost"),
            ("cabin", "gold"),
        ):
            animation = ChristmasTreeAnimation(_Controller(), {
                "scene_style": scene,
                "tree_palette": palette,
                "snowfall_density": 0.0,
                "seed": 7,
            })
            frame = animation.generate_frame(2.0, 0)
            fingerprints.add(hash(frame.tobytes()))

        self.assertEqual(len(fingerprints), 4)

    def test_small_wall_clamps_geometry_without_clipping(self):
        class SmallController:
            strip_count = 12
            leds_per_strip = 24
            total_leds = strip_count * leds_per_strip

        animation = ChristmasTreeAnimation(SmallController(), {"tree_height": 110})
        frame = animation.generate_frame(0.0, 0)

        self.assertEqual(frame.shape, (SmallController.total_leds, 3))
        self.assertTrue(all(0 <= x < 12 and 0 <= y < 24 for x, y, _ in animation._tree_pixels))


if __name__ == "__main__":
    unittest.main()

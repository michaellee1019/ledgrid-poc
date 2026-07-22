"""Tests for the procedural Pixel Quest RPG animation."""

import unittest

import numpy as np

from animation import RenderedFrame
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.plugins.pixel_quest import PixelQuestAnimation


class PixelQuestAnimationTests(unittest.TestCase):
    def make_animation(self, strips=32, leds=140, **config):
        return PixelQuestAnimation(PreviewLEDController(strips, leds), config)

    @staticmethod
    def advance(animation, seconds, step=.1):
        for _ in range(round(seconds / step)):
            animation._advance_game(step)

    def test_animation_is_shipped(self):
        self.assertIn("pixel_quest", AnimationManager.ALLOWED_PLUGINS)

    def test_renders_canonical_frames_on_supported_layouts(self):
        for strips, leds in ((32, 140), (16, 140), (12, 40)):
            with self.subTest(strips=strips, leds=leds):
                animation = self.make_animation(strips, leds)
                result = animation.generate_frame(0.0, 0)
                animation.generate_frame(.03, 1)
                self.assertIsInstance(result, RenderedFrame)
                self.assertEqual(result.pixels.shape, (strips * leds, 3))
                self.assertEqual(result.pixels.dtype, np.uint8)
                self.assertGreater(np.count_nonzero(result.pixels), strips * leds)

    def test_seed_repeats_procedural_stages(self):
        first = self.make_animation(seed=44)
        second = self.make_animation(seed=44)
        first._begin_stage(5)
        second._begin_stage(5)
        self.assertEqual(first.biome, second.biome)
        self.assertEqual(first.stage_seed, second.stage_seed)
        self.assertEqual(first.encounter_thresholds, second.encounter_thresholds)
        self.assertEqual(first.stage_start_ratio, second.stage_start_ratio)

        first._begin_stage(6)
        self.assertNotEqual(first.stage_seed, second.stage_seed)

    def test_generated_routes_explore_both_sides(self):
        animation = self.make_animation(seed=91)
        positions = []
        for stage in range(1, 7):
            animation._begin_stage(stage)
            positions.extend(animation._route_x(p) for p in np.linspace(0.0, 1.0, 50))
        self.assertLess(min(positions), animation.width * .30)
        self.assertGreater(max(positions), animation.width * .70)

    def test_progression_produces_levels_powerups_and_bosses(self):
        animation = self.make_animation(seed=1986)
        self.advance(animation, 240.0)
        stats = animation.get_runtime_stats()
        self.assertGreaterEqual(stats["longest_run_seconds"], 180.0)
        self.assertGreaterEqual(stats["hero_level"], 3)
        self.assertGreaterEqual(stats["powerups_collected"], 6)
        self.assertGreaterEqual(stats["bosses_defeated"], 1)
        self.assertGreaterEqual(stats["stage"], 5)

    def test_bosses_and_monsters_scale_with_stage(self):
        animation = self.make_animation(seed=12)
        animation._begin_stage(1)
        animation.encounter_index = 0
        animation._spawn_monster()
        early_level = animation.current_monster.level
        self.assertFalse(animation.current_monster.boss)

        animation._begin_stage(4)
        animation.encounter_index = len(animation.encounter_thresholds) - 1
        animation._spawn_monster()
        self.assertTrue(animation.current_monster.boss)
        self.assertGreater(animation.current_monster.level, early_level)
        self.assertGreater(animation.current_monster.max_hp, 80)

    def test_visual_settings_do_not_change_gameplay(self):
        visible = self.make_animation(seed=67, show_hud=True)
        hidden = self.make_animation(seed=67, show_hud=False)
        for _ in range(900):
            visible._advance_game(.1)
            hidden._advance_game(.1)
        self.assertEqual(visible.logical_state(), hidden.logical_state())

    def test_hero_palette_is_distinct_from_green_biomes(self):
        hero = np.asarray(PixelQuestAnimation.HERO_CYAN, dtype=np.int16)
        for terrain in ((10, 64, 42), (4, 29, 35)):
            self.assertGreater(np.linalg.norm(hero - np.asarray(terrain, dtype=np.int16)), 150)

    def test_render_cap_reuses_unchanged_frame(self):
        animation = self.make_animation()
        first = animation.generate_frame(0.0, 0)
        skipped = animation.generate_frame(.001, 1)
        advanced = animation.generate_frame(.03, 2)
        self.assertTrue(first.changed)
        self.assertFalse(skipped.changed)
        self.assertIs(first.pixels, skipped.pixels)
        self.assertTrue(advanced.changed)


if __name__ == "__main__":
    unittest.main()

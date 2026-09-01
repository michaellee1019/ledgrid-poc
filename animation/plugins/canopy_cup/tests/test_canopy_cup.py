"""Behavioral coverage for Canopy Cup: Impossible Ascent."""

import hashlib
import unittest

import numpy as np

from animation import RenderedFrame
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.plugins.canopy_cup import CanopyCupAnimation, Platform


class CanopyCupAnimationTests(unittest.TestCase):
    def make_animation(self, strips=32, leds=138, **config):
        return CanopyCupAnimation(PreviewLEDController(strips, leds), config)

    def test_animation_is_shipped_from_manifest(self):
        self.assertIn("canopy_cup", AnimationManager.ALLOWED_PLUGINS)

    def test_renders_canonical_frames_on_supported_layouts(self):
        for strips, leds in ((32, 138), (16, 138), (8, 40)):
            with self.subTest(strips=strips, leds=leds):
                animation = self.make_animation(strips, leds)
                rendered = animation.generate_frame(0.0, 0)
                self.assertIsInstance(rendered, RenderedFrame)
                self.assertEqual(rendered.pixels.shape, (strips * leds, 3))
                self.assertEqual(rendered.pixels.dtype, np.uint8)
                self.assertTrue(rendered.pixels.flags.c_contiguous)
                self.assertGreater(np.count_nonzero(rendered.pixels), strips * leds)

    def test_source_rate_uses_ticks_and_reuses_cached_frame(self):
        animation = self.make_animation()
        results = [animation.generate_frame(index / 200.0, index) for index in range(200)]
        changed = sum(result.changed for result in results)
        self.assertGreaterEqual(changed, 29)
        self.assertLessEqual(changed, 31)
        self.assertIs(results[1].pixels, results[0].pixels)

    def test_late_first_call_and_stall_have_bounded_catchup(self):
        animation = self.make_animation()
        animation.generate_frame(500.0, 0)
        self.assertEqual(animation.fixed_steps, 0)
        animation.generate_frame(501.0, 1)
        self.assertLessEqual(animation.fixed_steps, animation.MAX_STEPS)
        self.assertGreater(animation.dropped_catchup_seconds, 0.8)

    def test_course_is_reachable_across_seeds_and_difficulties(self):
        for seed in range(16):
            for difficulty in (.6, 1.0, 1.4):
                animation = self.make_animation(seed=seed, course_difficulty=difficulty)
                animation._start_heat(seed % 4)
                self.assertTrue(animation.course_is_reachable())
                self.assertGreater(len(animation.route_platforms), 10)

    def test_landing_uses_swept_crossing_and_moving_surface(self):
        animation = self.make_animation()
        animation._start_heat(0)
        racer = animation.racers[0]
        platform = Platform(5.0, 40.0, 8.0, "moving", 3, amplitude=2.0, phase=.5, rate=1.0)
        animation.platforms = [platform]
        racer.x, racer.y, racer.vy = 7.0, 37.6, 30.0
        racer.grounded = False
        animation._integrate_racer(racer, .02, 0.0)
        self.assertAlmostEqual(racer.y, 38.0)
        self.assertTrue(racer.grounded)
        self.assertEqual(racer.grounded_platform, 0)

    def test_web_rope_enforces_length_and_removes_outward_velocity(self):
        animation = self.make_animation()
        animation._start_heat(0)
        racer = animation.racers[0]
        racer.x, racer.y = 20.0, 60.0
        racer.vx, racer.vy = 12.0, 12.0
        racer.web_anchor = (10.0, 45.0)
        racer.web_length = 8.0
        racer.ability_time = 1.0
        animation.platforms = []
        animation._integrate_racer(racer, animation.PHYSICS_DT, 0.0)
        distance = np.hypot(racer.x + 1.0 - 10.0, racer.y + 1.0 - 45.0)
        self.assertLessEqual(distance, 8.00001)

    def test_all_signature_abilities_appear_in_one_heat(self):
        animation = self.make_animation(seed=42, enemy_density=0.0)
        animation._start_heat(0)
        for _ in range(24 * 120):
            animation._fixed_step(animation.PHYSICS_DT)
        self.assertTrue(all(count > 0 for count in animation.ability_uses))
        self.assertTrue(any(racer.finished for racer in animation.racers))

    def test_heat_scoring_and_final_multiplier(self):
        animation = self.make_animation(qualifying_heats=2)
        animation._start_heat(0)
        for racer, finish in zip(animation.racers, (4.0, 1.0, None, 2.0)):
            racer.finish_time = finish
            racer.finished = finish is not None
            racer.best_y = 20.0 + racer.index
        animation._finish_heat()
        self.assertEqual(animation.points, [2, 5, 1, 3])

        animation._start_heat(2)
        for racer, finish in zip(animation.racers, (1.0, 2.0, 3.0, 4.0)):
            racer.finish_time = finish
            racer.finished = True
        animation._finish_heat()
        self.assertEqual(animation.points, [12, 11, 5, 5])

    def test_default_tournament_duration_is_about_seven_minutes(self):
        animation = self.make_animation(enemy_density=0.0, rivalry=0.0)
        elapsed = 0.0
        while animation.tournament_index == 0 and elapsed < 440.0:
            animation._fixed_step(animation.PHYSICS_DT)
            elapsed += animation.PHYSICS_DT
        self.assertEqual(animation.tournament_index, 1)
        self.assertGreaterEqual(elapsed, 390.0)
        self.assertLessEqual(elapsed, 430.0)

    def test_every_racer_can_win_across_seeded_heats(self):
        winners = set()
        fully_resolved = 0
        for seed in range(32):
            animation = self.make_animation(seed=seed, enemy_density=.55)
            animation._start_heat(seed % 7)
            for _ in range(44 * 120):
                animation._fixed_step(animation.PHYSICS_DT)
            fully_resolved += all(racer.finished for racer in animation.racers)
            for _ in range(5 * 120):
                if animation.phase != "race":
                    break
                animation._fixed_step(animation.PHYSICS_DT)
            winners.add(animation.heat_results[0])
        self.assertEqual(winners, {0, 1, 2, 3})
        self.assertGreaterEqual(fully_resolved, 31)

    def test_themes_are_visually_distinct_but_logically_identical(self):
        fingerprints = set()
        baseline_state = None
        for theme in CanopyCupAnimation.THEMES[1:]:
            animation = self.make_animation(seed=81, world_theme=theme)
            animation._start_heat(0)
            for _ in range(120):
                animation._fixed_step(animation.PHYSICS_DT)
            state = animation.logical_state()
            if baseline_state is None:
                baseline_state = state
            else:
                self.assertEqual(state, baseline_state)
            animation._render()
            fingerprints.add(hashlib.sha256(animation._canvas.tobytes()).hexdigest())
        self.assertEqual(len(fingerprints), len(CanopyCupAnimation.THEMES) - 1)

    def test_runtime_stats_expose_tournament_and_bounded_entities(self):
        animation = self.make_animation(enemy_density=1.0)
        animation._start_heat(0)
        for _ in range(20 * 120):
            animation._fixed_step(animation.PHYSICS_DT)
        stats = animation.get_runtime_stats()
        self.assertEqual(stats["heat"], 1)
        self.assertEqual(set(stats["standings"]), {
            "Web-Wisp", "Barrelback", "Glimmer Fern", "Ivory Wayfarer",
        })
        self.assertLessEqual(stats["active_enemies"], animation.MAX_ENEMIES)
        self.assertTrue(stats["course_reachable"])


if __name__ == "__main__":
    unittest.main()

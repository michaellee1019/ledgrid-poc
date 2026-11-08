"""Behavior coverage for the self-playing Baja Rally animation."""

import unittest

import numpy as np

from animation import RenderedFrame
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.plugins.baja_rally import BajaRallyAnimation, Hazard


class BajaRallyAnimationTests(unittest.TestCase):
    def make_animation(self, strips=32, leds=138, **config):
        return BajaRallyAnimation(PreviewLEDController(strips, leds), config)

    def test_animation_is_shipped_from_manifest(self):
        self.assertIn("baja_rally", AnimationManager.ALLOWED_PLUGINS)

    def test_renders_canonical_frames_on_supported_layouts(self):
        for strips, leds in ((33, 138), (32, 138), (16, 138), (8, 40)):
            with self.subTest(strips=strips, leds=leds):
                animation = self.make_animation(strips, leds)
                rendered = animation.generate_frame(0.0, 0)
                self.assertIsInstance(rendered, RenderedFrame)
                self.assertEqual(rendered.pixels.shape, (strips * leds, 3))
                self.assertEqual(rendered.pixels.dtype, np.uint8)
                self.assertTrue(rendered.pixels.flags.c_contiguous)
                self.assertGreater(np.count_nonzero(rendered.pixels), strips * leds)

    def test_opening_frame_shows_the_truck_and_spreads_the_field(self):
        animation = self.make_animation()
        animation._render()
        body = np.asarray(animation.hero.body, dtype=np.uint8)
        self.assertTrue(np.any(np.all(animation._canvas == body, axis=2)))
        road = animation._road_slice()
        rows = []
        for truck in animation.trucks:
            box = animation._truck_box(truck.progress, truck.lane, road)
            self.assertIsNotNone(box)
            rows.append(box[1])
        self.assertEqual(rows[0], max(rows))
        self.assertGreater(max(rows) - min(rows), animation.height * 0.2)

    def test_chase_camera_puts_the_hero_low_and_rivals_higher(self):
        animation = self.make_animation()
        hero = animation._screen_t(animation.CAMERA_BACK)
        nearest = animation._screen_t(animation.CAMERA_BACK + animation.START_GAPS[1])
        leader = animation._screen_t(animation.CAMERA_BACK + animation.START_GAPS[-1])
        self.assertGreater(hero, 0.75)
        self.assertGreater(hero, nearest + 0.06)
        self.assertGreater(nearest, leader + 0.15)

    def test_source_rate_reuses_cached_frames_without_changing_rules(self):
        slow = self.make_animation(render_fps=24, seed=11)
        fast = self.make_animation(render_fps=60, seed=11)
        for index in range(241):
            elapsed = index / 120.0
            slow.generate_frame(elapsed, index)
            fast.generate_frame(elapsed, index)
        self.assertEqual(slow.logical_state(), fast.logical_state())

        animation = self.make_animation(render_fps=40)
        results = [animation.generate_frame(index / 200.0, index) for index in range(200)]
        changed = sum(result.changed for result in results)
        self.assertGreaterEqual(changed, 39)
        self.assertLessEqual(changed, 41)
        self.assertFalse(results[1].changed)
        self.assertIs(results[1].pixels, results[0].pixels)

    def test_late_first_call_and_stall_have_bounded_catchup(self):
        animation = self.make_animation()
        animation.generate_frame(500.0, 0)
        self.assertEqual(animation.fixed_steps, 0)
        animation.generate_frame(501.0, 1)
        self.assertLessEqual(animation.fixed_steps, animation.MAX_STEPS)
        self.assertGreater(animation.dropped_catchup_seconds, 0.7)

    def test_same_seed_rebuilds_the_same_course(self):
        first = self.make_animation(seed=44)
        second = self.make_animation(seed=44)
        first._start_leg(2)
        second._start_leg(2)
        self.assertEqual(first.course.biome, second.course.biome)
        self.assertEqual(first.course.seed, second.course.seed)
        np.testing.assert_allclose(first.course.line, second.course.line)
        third = self.make_animation(seed=45)
        third._start_leg(2)
        self.assertFalse(np.allclose(first.course.line, third.course.line))

    def test_visual_settings_do_not_change_the_race(self):
        shown = self.make_animation(seed=19, show_hud=True, time_of_day="night", brightness=0.4)
        hidden = self.make_animation(seed=19, show_hud=False, time_of_day="day", brightness=1.0)
        for index in range(90):
            elapsed = index / 30.0
            shown.generate_frame(elapsed, index)
            hidden.generate_frame(elapsed, index)
        self.assertEqual(shown.logical_state(), hidden.logical_state())
        self.assertFalse(np.array_equal(
            shown.generate_frame(3.0, 90).pixels,
            hidden.generate_frame(3.0, 90).pixels,
        ))

    def test_time_of_day_and_biome_are_visibly_different(self):
        day = self.make_animation(time_of_day="day", biome="desert")
        night = self.make_animation(time_of_day="night", biome="coast")
        day._start_leg(0)
        night._start_leg(0)
        day._render()
        night._render()
        self.assertGreater(np.mean(np.abs(day._canvas.astype(int) - night._canvas.astype(int))), 12)

    def test_hills_change_the_road_without_erasing_it(self):
        animation = self.make_animation(seed=3, traffic=0.0)
        animation.course.elevation[:] = 0.0
        animation.course.jumps[:] = False
        animation._render()
        flat = animation._canvas.copy()
        animation.course.elevation[:] = np.sin(np.linspace(0.0, 8.0, animation.course.elevation.shape[0]))
        animation._render()
        road = animation._canvas[animation.horizon:, :, :]
        self.assertGreater(np.mean(np.abs(flat.astype(int) - animation._canvas.astype(int))), 2.0)
        self.assertGreater(np.count_nonzero(road), road.size // 3)

    def test_rock_damages_a_truck_and_air_carries_it_over(self):
        grounded = self._isolated_hero()
        grounded.course.hazards = (Hazard(0, 40.0, 0.0, "rock"),)
        grounded.hero.progress = 38.4
        grounded.hero.lane = 0.0
        grounded.hero.speed = 24.0
        grounded._fixed_step(grounded.SIM_DT)
        self.assertGreater(grounded.hero.damage, 0.05)
        self.assertIn(0, grounded.consumed)

        flying = self._isolated_hero()
        flying.course.hazards = (Hazard(0, 40.0, 0.0, "rock"),)
        flying.hero.progress = 38.4
        flying.hero.lane = 0.0
        flying.hero.speed = 24.0
        flying.hero.airborne = 0.8
        flying.hero.air_total = 0.8
        flying._fixed_step(flying.SIM_DT)
        self.assertEqual(flying.hero.damage, 0.0)
        self.assertNotIn(0, flying.consumed)

    def test_money_bag_pays_the_truck_that_drives_over_it(self):
        animation = self._isolated_hero()
        animation.course.hazards = (Hazard(3, 40.0, 0.0, "money"),)
        animation.hero.progress = 38.4
        animation.hero.lane = 0.0
        animation.hero.speed = 24.0
        animation.hero.cash = 10
        animation._fixed_step(animation.SIM_DT)
        self.assertEqual(animation.hero.cash, 90)
        self.assertEqual(animation.hero.money_bags, 1)
        self.assertEqual(animation.hero.damage, 0.0)

    def test_downhill_launches_a_fast_truck(self):
        animation = self._isolated_hero()
        animation.course.jumps[:] = False
        window = (animation.course.samples > 36.0) & (animation.course.samples < 46.0)
        animation.course.jumps[window] = True
        animation.hero.progress = 34.0
        animation.hero.speed = 24.0
        animation.hero.was_on_jump = False
        for _ in range(24):
            animation._fixed_step(animation.SIM_DT)
            if animation.hero.airborne > 0.0 or animation.hero.landings:
                break
        self.assertTrue(animation.hero.airborne > 0.0 or animation.hero.landings > 0)

    def test_nitro_pulls_the_hero_ahead_of_an_equal_rival(self):
        animation = self._isolated_hero()
        hero = animation.hero
        rival = animation.trucks[1]
        rival.finished = False
        rival.progress = 80.0
        rival.lane = 0.55
        rival.speed = 20.0
        rival.nitro = 0.0
        rival.boosting = 0.0
        hero.progress = 20.0
        hero.lane = -0.55
        hero.speed = 20.0
        hero.nitro = 0.2
        hero.boosting = 2.0
        for _ in range(30):
            animation._fixed_step(animation.SIM_DT)
        self.assertGreater(hero.progress - 20.0, (rival.progress - 80.0) + 1.5)

    def test_leg_places_pay_distinct_points_and_a_short_race_finishes(self):
        animation = self.make_animation(seed=7)
        times = (3.0, 1.0, 4.0, 2.0, 5.0)
        for truck, finish in zip(animation.trucks, times):
            truck.finished = True
            truck.finish_time = finish
        animation.phase = "race"
        animation._close_leg()
        self.assertEqual(animation.points, [3, 8, 2, 5, 1])
        self.assertEqual(animation.phase, "pit")

        running = self.make_animation(seed=8, traffic=0.4)
        running.leg_length_override = 100.0
        running._start_leg(0)
        running.hold = 0.0
        for _ in range(30 * 20):
            if running.phase != "race":
                break
            running._fixed_step(running.SIM_DT)
        self.assertEqual(running.phase, "pit")
        self.assertEqual(sum(running.points), 19)

    def test_pit_buys_one_upgrade_and_does_not_repeat_it(self):
        animation = self.make_animation()
        hero = animation.hero
        hero.cash = 1000
        hero.hazard_hits = 6
        hero.landings = 4
        animation.phase = "pit"
        animation.phase_time = 0.0
        animation.pit_applied = False
        animation._fixed_step(animation.SIM_DT)
        self.assertEqual(hero.shocks, 2)
        self.assertEqual(hero.cash, 850)
        self.assertEqual(hero.last_upgrade, "shocks")
        animation._fixed_step(animation.SIM_DT)
        self.assertEqual(hero.shocks, 2)
        self.assertEqual(hero.cash, 850)

    def test_hero_races_the_field_and_more_than_one_truck_can_win(self):
        animation = self.make_animation(seed=3, traffic=0.7, difficulty=1.0)
        animation.hold = 0.0
        for _ in range(30 * 18):
            animation._fixed_step(animation.SIM_DT)
        self.assertGreater(animation.hero.career_distance, 180.0)
        self.assertGreater(animation.overtakes, 0)
        self.assertLess(animation.hero.damage, 1.0)

        winners = set()
        hero_places = []
        for seed in range(10):
            race = self.make_animation(seed=seed, traffic=0.8, difficulty=1.0)
            race.leg_length_override = 260.0
            race._start_leg(0)
            race.hold = 0.0
            for _ in range(30 * 24):
                if race.phase != "race":
                    break
                race._fixed_step(race.SIM_DT)
            self.assertEqual(race.phase, "pit", seed)
            order = sorted(range(5), key=lambda index: (-race.points[index], index))
            winners.add(order[0])
            hero_places.append(order.index(0))
        self.assertGreaterEqual(len(winners), 2, winners)
        self.assertLessEqual(sum(hero_places) / len(hero_places), 2.0, hero_places)

    def test_live_visual_update_does_not_reset_progress(self):
        animation = self.make_animation(seed=5)
        animation.hold = 0.0
        animation._fixed_step(animation.SIM_DT)
        progress = animation.hero.progress
        steps = animation.fixed_steps
        animation.update_parameters({"speed": 1.6, "time_of_day": "dusk", "show_hud": False})
        self.assertEqual(animation.hero.progress, progress)
        self.assertEqual(animation.fixed_steps, steps)
        self.assertEqual(animation.params["time_of_day"], "dusk")

    def _isolated_hero(self):
        animation = self.make_animation(seed=2, traffic=0.0)
        animation.hold = 0.0
        animation.course.line[:] = 0.0
        animation.course.elevation[:] = 0.0
        animation.course.jumps[:] = False
        animation.course.hazards = ()
        animation.course.grip[:] = 1.0
        for truck in animation.trucks[1:]:
            truck.finished = True
            truck.finish_time = 0.0
            truck.speed = 0.0
            truck.progress = 1.0
        return animation


if __name__ == "__main__":
    unittest.main()

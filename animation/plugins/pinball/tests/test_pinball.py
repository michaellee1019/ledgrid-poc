"""Tests for the CPU-light portrait pinball animation."""

import hashlib
import unittest

import numpy as np

from animation import RenderedFrame
from animation.core.manager import PreviewLEDController
from animation.core.plant_awareness import InstallationGeometryContact, PlantMaskGeometry
from animation.core.presentation_contracts import ResolvedScene
from animation.plugins.pinball import PinballAnimation


class PinballAnimationTests(unittest.TestCase):
    @staticmethod
    def _contact(*, core_x=10, core_led=20, clearance_radius=2, identity=("test",)):
        shape = (33, 138)
        empty = np.zeros(shape, dtype=bool)
        cores = empty.copy()
        clearance = empty.copy()
        cores[core_x, core_led] = True
        clearance[
            max(0, core_x - clearance_radius):core_x + clearance_radius + 1,
            max(0, core_led - clearance_radius):core_led + clearance_radius + 1,
        ] = True
        distance = np.zeros(shape, dtype=np.float32)
        geometry = PlantMaskGeometry(
            foliage=empty,
            globes=cores,
            obstacle=cores,
            clearance=clearance,
            foliage_flat=empty.reshape(-1),
            globes_flat=cores.reshape(-1),
            obstacle_flat=cores.reshape(-1),
            clearance_flat=clearance.reshape(-1),
            foliage_count=0,
            globe_count=1,
            globe_regions=0,
            foliage_edge=empty,
            globe_edge=cores,
            obstacle_edge=cores,
            distance=distance,
            normal_x=distance,
            normal_y=distance,
            globe_region_masks={},
        )
        return InstallationGeometryContact.from_geometry(geometry, identity=identity)

    @staticmethod
    def _context(contact, strength=1.0):
        return ResolvedScene(
            canonical_scene={"plants": {"effects": {
                "version": 1,
                "active": ["bumper"] if strength is not None else [],
                "strengths": {} if strength is None else {"bumper": strength},
            }}},
            canonical_bytes=b"pinball-contact-test",
            digest="pinball-contact-test",
            descriptor=PinballAnimation.component_descriptor(),
            parameters=PinballAnimation.DEFAULTS,
            palette={"palette_id": "neutral"},
            phase_time=0.0,
            plant_inputs={},
            installation_geometry=contact,
        )

    @staticmethod
    def _state(animation):
        return (
            animation.ball_x, animation.ball_y, animation.ball_vx, animation.ball_vy,
            animation.score, animation.streak, animation._sim_time, animation.last_elapsed,
        )

    def test_renders_canonical_frame_for_actual_grid(self):
        controller = PreviewLEDController(strips=32, leds_per_strip=140)
        animation = PinballAnimation(controller)

        result = animation.generate_frame(0.0, 0)

        self.assertIsInstance(result, RenderedFrame)
        self.assertEqual(result.pixels.shape, (4480, 3))
        self.assertEqual(result.pixels.dtype, np.uint8)
        self.assertTrue(result.changed)
        self.assertGreater(np.count_nonzero(result.pixels), 300)

    def test_adapts_to_hat_and_small_preview_layouts(self):
        for strips, leds in ((16, 140), (12, 40), (6, 20)):
            with self.subTest(strips=strips, leds=leds):
                animation = PinballAnimation(PreviewLEDController(strips, leds))
                result = animation.generate_frame(0.1, 1)
                self.assertEqual(result.pixels.shape, (strips * leds, 3))

    def test_render_cap_returns_cached_unchanged_frame(self):
        animation = PinballAnimation(
            PreviewLEDController(32, 140), {"render_fps": 100.0}
        )
        first = animation.generate_frame(0.0, 0)
        skipped = animation.generate_frame(0.005, 1)
        advanced = animation.generate_frame(0.02, 2)

        self.assertTrue(first.changed)
        self.assertFalse(skipped.changed)
        self.assertIs(skipped.pixels, first.pixels)
        self.assertTrue(advanced.changed)

    def test_defaults_to_100_fps_simulation(self):
        animation = PinballAnimation(PreviewLEDController(32, 140))

        self.assertEqual(animation.params["render_fps"], 100.0)
        self.assertEqual(
            animation.get_parameter_schema()["render_fps"]["default"],
            100.0,
        )

    def test_exact_100_hz_timestamps_are_not_dropped(self):
        animation = PinballAnimation(PreviewLEDController(32, 140))

        results = [animation.generate_frame(index / 100.0, index) for index in range(101)]

        self.assertTrue(all(result.changed for result in results))

    def test_bumper_hit_increments_score_and_streak(self):
        animation = PinballAnimation(PreviewLEDController(32, 140))
        bx, by = animation._bumper_positions()[0]
        animation.ball_x = bx + 0.2
        animation.ball_y = by
        animation.ball_vx = -5.0
        animation.ball_vy = 0.0
        original_score = animation.score

        animation.generate_frame(0.0, 0)
        animation.generate_frame(0.02, 1)

        self.assertGreater(animation.score, original_score)
        self.assertEqual(animation.streak, 1)
        self.assertGreater(len(animation.bursts), 0)

    def test_minigame_and_failure_have_visible_state(self):
        animation = PinballAnimation(PreviewLEDController(32, 140))
        animation._start_minigame()
        self.assertNotEqual(animation.mode, "READY")
        self.assertGreater(animation.multiplier, 1)

        animation._drain()
        self.assertGreater(animation.drain_time, 0)
        self.assertGreater(animation._failure_flash, 0)
        self.assertEqual(animation.streak, 0)

    def test_primary_gesture_is_queued_bounded_and_applied_once_on_a_source_tick(self):
        animation = PinballAnimation(PreviewLEDController(33, 138), {"seed": 95})
        animation.ball_x, animation.ball_y = 16.0, 60.0
        animation.ball_vx, animation.ball_vy = 3.0, -20.0
        animation.generate_frame(0.0, 0)
        original_velocity = (animation.ball_vx, animation.ball_vy)
        original_rng = animation.random.getstate()
        original_score = animation.score

        self.assertTrue(animation.handle_interaction("primary", 0.0, 60.0, 1.0))
        self.assertFalse(animation.handle_interaction("secondary", 0.0, 60.0, 1.0))
        self.assertFalse(animation.handle_interaction("primary", -1.0, 60.0, 1.0))
        self.assertFalse(animation.handle_interaction("primary", 0.0, 138.0, 1.0))
        self.assertFalse(animation.handle_interaction("primary", 0.0, 60.0, 0.0))
        self.assertTrue(animation.get_runtime_stats()["primary_interaction_pending"])

        # Cache invalidation creates a fresh frame without simulating a second
        # tick or consuming the queued flipper input early.
        invalidated = animation.generate_frame(0.0, 1)
        self.assertTrue(invalidated.changed)
        self.assertEqual((animation.ball_vx, animation.ball_vy), original_velocity)
        self.assertEqual(animation.random.getstate(), original_rng)
        self.assertEqual(animation.score, original_score)

        animation.generate_frame(0.02, 2)
        stats = animation.get_runtime_stats()
        self.assertEqual(stats["primary_interactions_received"], 1)
        self.assertEqual(stats["primary_interactions_applied"], 1)
        self.assertFalse(stats["primary_interaction_pending"])
        self.assertLessEqual(np.hypot(animation.ball_vx, animation.ball_vy), animation.MAX_PRIMARY_SPEED)
        self.assertNotEqual((animation.ball_vx, animation.ball_vy), original_velocity)
        self.assertEqual(animation.random.getstate(), original_rng)
        self.assertEqual(animation.score, original_score)

    def test_primary_gesture_discards_a_burst_without_deferred_work(self):
        animation = PinballAnimation(PreviewLEDController(33, 138), {"seed": 95})
        animation.ball_x, animation.ball_y = 16.0, 60.0
        animation.ball_vx, animation.ball_vy = 0.0, -12.0
        animation.generate_frame(0.0, 0)
        for index in range(100):
            self.assertTrue(animation.handle_interaction("primary", float(index % 33), 60.0, 1.0))
        animation.generate_frame(0.02, 1)
        stats = animation.get_runtime_stats()
        self.assertEqual(stats["primary_interactions_received"], 100)
        self.assertEqual(stats["primary_interactions_applied"], 1)
        self.assertFalse(stats["primary_interaction_pending"])
        self.assertLessEqual(np.hypot(animation.ball_vx, animation.ball_vy), animation.MAX_PRIMARY_SPEED)

    def test_no_input_sequence_preserves_frame_state_rng_and_cache_contract(self):
        animation = PinballAnimation(PreviewLEDController(33, 138), {"seed": 95})
        sequence = [animation.generate_frame(time, index) for index, time in enumerate((0.0, 0.005, 0.01, 0.02, 0.04, 0.08))]
        self.assertEqual([frame.changed for frame in sequence], [True, False, True, True, True, True])
        self.assertIs(sequence[1].pixels, sequence[0].pixels)
        digest = hashlib.sha256(b"".join(frame.pixels.tobytes() for frame in sequence)).hexdigest()
        self.assertEqual(digest, "7183645735087e899eef42604ce4605c0c72bf9f5b562b747abd84708e1d07a0")
        self.assertEqual(
            (
                animation.ball_x, animation.ball_y, animation.ball_vx,
                animation.ball_vy, animation.score, animation.streak,
                animation.multiplier, animation.balls, animation.mode,
                animation._sim_time, animation.last_elapsed,
                animation.last_render_elapsed,
            ),
            (28.244, 112.03493925, -7.0, -72.652, 95000, 0, 1, 3,
             "READY", 0.10800000000000001, 0.08, 0.08),
        )
        self.assertEqual(animation.score, 95000)
        self.assertEqual(animation.random.getstate(), PinballAnimation(PreviewLEDController(33, 138), {"seed": 95}).random.getstate())
        self.assertEqual(animation.get_runtime_stats()["primary_interactions_applied"], 0)

    def test_declares_the_narrow_local_primary_capability(self):
        self.assertEqual(PinballAnimation.INTERACTION_TYPES, frozenset(("primary",)))
        self.assertEqual(PinballAnimation.COMPOSER_INTERACTIONS["point"]["kind"], "primary")

    def test_exact_globe_core_scores_once_and_clearance_never_collides(self):
        animation = PinballAnimation(PreviewLEDController(33, 138), {"seed": 91})
        animation.set_presentation_context(self._context(self._contact()))
        core_y = 138 - 1 - 20
        start_score = animation.score

        # This path crosses clearance at x=8 but never enters the x=10 core.
        animation.ball_x, animation.ball_y = 8.0, float(core_y)
        animation.ball_vx, animation.ball_vy = 12.0, 0.0
        animation._collide_with_globe_cores(7.0, float(core_y))
        self.assertEqual(animation.score, start_score)

        # Sweeping through the exact core cannot tunnel; one boundary means one
        # award even if the next check is still overlapping the core.
        animation.ball_x, animation.ball_y = 12.0, float(core_y)
        animation.ball_vx, animation.ball_vy = 24.0, 0.0
        animation._collide_with_globe_cores(8.0, float(core_y))
        self.assertEqual(animation.score, start_score + 2500)
        self.assertLess(animation.ball_vx, 0.0)
        self.assertEqual(animation.get_runtime_stats()["globe_bumper_hits"], 1)
        animation._collide_with_globe_cores(8.0, float(core_y))
        self.assertEqual(animation.score, start_score + 2500)

    def test_off_zero_and_missing_contact_are_exact_noops(self):
        controller = PreviewLEDController(33, 138)
        ordinary = PinballAnimation(controller, {"seed": 317})
        zero = PinballAnimation(controller, {"seed": 317})
        missing = PinballAnimation(controller, {"seed": 317})
        contact = self._contact()
        ordinary.set_presentation_context(self._context(None, None))
        zero.set_presentation_context(self._context(contact, 0.0))
        missing.set_presentation_context(self._context(
            InstallationGeometryContact.unavailable(33, 138, status="missing"), 1.0
        ))
        for index, elapsed in enumerate((0.0, 0.02, 0.04, 0.08, 0.12)):
            baseline = ordinary.generate_frame(elapsed, index)
            for candidate in (zero, missing):
                result = candidate.generate_frame(elapsed, index)
                np.testing.assert_array_equal(result.pixels, baseline.pixels)
                self.assertEqual(self._state(candidate), self._state(ordinary))
                self.assertEqual(candidate.random.getstate(), ordinary.random.getstate())

    def test_unchanged_resolved_context_keeps_the_100hz_cache_for_every_noop_path(self):
        contact = self._contact()
        contexts = (
            self._context(None, None),
            self._context(contact, 0.0),
            self._context(InstallationGeometryContact.unavailable(33, 138, status="missing"), 1.0),
        )
        for context in contexts:
            animation = PinballAnimation(PreviewLEDController(33, 138), {"seed": 317})
            first = animation.render_resolved_scene(context)
            state = self._state(animation)
            rng = animation.random.getstate()
            cached = animation.render_resolved_scene(context)
            self.assertTrue(first.changed)
            self.assertFalse(cached.changed)
            self.assertIs(cached.pixels, first.pixels)
            self.assertEqual(self._state(animation), state)
            self.assertEqual(animation.random.getstate(), rng)

    def test_many_globe_boundaries_never_exceed_the_pinball_speed_cap(self):
        animation = PinballAnimation(PreviewLEDController(33, 138), {"seed": 91})
        animation.set_presentation_context(self._context(self._contact()))
        core_y = float(138 - 1 - 20)
        starting_score = animation.score
        speed = 24.0
        for _ in range(12):
            animation._globe_contact_active = False
            animation.ball_x, animation.ball_y = 12.0, core_y
            animation.ball_vx, animation.ball_vy = speed, 0.0
            animation._collide_with_globe_cores(8.0, core_y)
            speed = abs(animation.ball_vx)
            self.assertLessEqual(np.hypot(animation.ball_vx, animation.ball_vy), animation.MAX_PRIMARY_SPEED)
        self.assertEqual(animation.score, starting_score + 12 * 2500)
        self.assertEqual(animation.get_runtime_stats()["globe_bumper_hits"], 12)

    def test_live_contact_refresh_only_invalidates_derived_views(self):
        animation = PinballAnimation(PreviewLEDController(33, 138), {"seed": 73})
        first = self._contact(identity=("first",))
        second = self._contact(core_x=12, identity=("second",))
        animation.set_presentation_context(self._context(first))
        before = self._state(animation)
        rng = animation.random.getstate()
        animation.last_render_elapsed = 6.0

        animation.set_presentation_context(self._context(second))

        self.assertEqual(self._state(animation), before)
        self.assertEqual(animation.random.getstate(), rng)
        self.assertEqual(animation.get_runtime_stats()["globe_bumper_hits"], 0)
        self.assertIsNone(animation.last_render_elapsed)
        self.assertTrue(animation._globe_cores[117, 12])


if __name__ == "__main__":
    unittest.main()

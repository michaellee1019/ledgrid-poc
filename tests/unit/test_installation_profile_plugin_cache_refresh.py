"""Cross-plugin acceptance for live managed geometry cache refresh."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.core.installation_profile import (
    decode_installation_profile,
    encode_installation_profile,
)
from animation.core.installation_profile_library import InstallationProfileLibrary
from animation.core.installation_profile_runtime import InstallationProfileRuntimeView
from animation.core.installation_profile_topology import (
    INSTALLED_INSTALLATION_PROFILE_TOPOLOGY,
)
from animation.core.presentation_contracts import AnimationRuntimeContext
from animation.plugins.ascii_drop import AsciiDropAnimation
from animation.plugins.canopy_cup import CanopyCupAnimation
from animation.plugins.christmas_tree import ChristmasTreeAnimation
from animation.plugins.conway_life import ConwayLifeAnimation
from animation.plugins.emoji_arranger import EmojiArrangerAnimation
from animation.plugins.gif_animation import GifAnimation
from animation.plugins.gradient import GradientAnimation
from animation.plugins.living_ecosystem import LivingEcosystemAnimation
from animation.plugins.maze_chase import MazeChaseAnimation
from animation.plugins.pinball import PinballAnimation
from animation.plugins.plant_calibration import PlantCalibrationAnimation
from animation.plugins.pixel_chase import PixelChaseAnimation
from animation.plugins.pixel_quest import PixelQuestAnimation
from animation.plugins.snake import SnakeAnimation
from animation.plugins.strip_order import StripOrderAnimation
from animation.plugins.tetris import TetrisAnimation
from animation.plugins.world_flags import WorldFlagsAnimation


ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = ROOT / "tests" / "fixtures" / "installation_profile_v1.bin"


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip
    debug = False


def _context(view: InstallationProfileRuntimeView) -> AnimationRuntimeContext:
    return AnimationRuntimeContext(
        wall_time=100.0,
        unscaled_elapsed=4.0,
        scaled_elapsed=4.0,
        frame_index=20,
        scene_epoch=8,
        global_width=33,
        height=138,
        local_strip_offset=0,
        local_width=33,
        vibe_id="neutral",
        vibe_profile_version=1,
        palette_roles={},
        capability_values={},
        installation_profile_view=view,
        plant_modifiers={},
    )


def _empty_profile_bytes(golden: bytes) -> bytes:
    profile = decode_installation_profile(golden)
    shape = profile.category.shape
    return encode_installation_profile(
        replace(
            profile,
            calibration_digest=b"\xa5" * 32,
            category=np.zeros(shape, dtype=np.uint8),
            clearance=np.zeros(shape, dtype=np.uint8),
            foliage_edge=np.zeros(shape, dtype=np.uint8),
            globe_edge=np.zeros(shape, dtype=np.uint8),
            obstacle_edge=np.zeros(shape, dtype=np.uint8),
            globe_region=np.zeros(shape, dtype=np.uint8),
            distance=np.full(shape, 255, dtype=np.uint8),
            normal_x=np.zeros(shape, dtype=np.int8),
            normal_y=np.zeros(shape, dtype=np.int8),
        )
    )


class InstallationProfilePluginCacheRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.library = InstallationProfileLibrary(Path(cls.temporary.name) / "library")
        golden = GOLDEN_PATH.read_bytes()
        first = cls.library.publish(golden)
        second = cls.library.publish(_empty_profile_bytes(golden))
        cls.populated = InstallationProfileRuntimeView.from_resolved(
            cls.library.resolve(first.id, INSTALLED_INSTALLATION_PROFILE_TOPOLOGY)
        )
        cls.empty = InstallationProfileRuntimeView.from_resolved(
            cls.library.resolve(second.id, INSTALLED_INSTALLATION_PROFILE_TOPOLOGY)
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_snake_refreshes_collision_and_portal_geometry_without_state_or_rng_change(self) -> None:
        animation = SnakeAnimation(
            _Controller(),
            {
                "seed": 91,
                "snake_count": 2,
                "plant_modifiers": {
                    "version": 1,
                    "active": ["obstacle"],
                    "strengths": {"obstacle": 1.0},
                },
            },
        )
        animation.set_presentation_context(_context(self.populated))
        self.assertTrue(animation._plant_obstacles)
        semantic = (
            deepcopy(animation.snakes),
            set(animation.food),
            animation.moves,
            animation.food_eaten,
            animation.deaths,
            animation._step_accumulator,
            animation.last_elapsed,
        )
        rng = animation.random.getstate()
        authored = animation.authored_params_snapshot()

        animation.set_presentation_context(_context(self.empty))

        self.assertFalse(animation._plant_obstacles)
        self.assertFalse(animation._plant_clearance)
        self.assertEqual(
            semantic,
            (
                animation.snakes,
                animation.food,
                animation.moves,
                animation.food_eaten,
                animation.deaths,
                animation._step_accumulator,
                animation.last_elapsed,
            ),
        )
        self.assertEqual(animation.random.getstate(), rng)
        self.assertEqual(animation.authored_params_snapshot(), authored)

    def test_conway_refreshes_current_geometry_and_next_plan_only(self) -> None:
        animation = ConwayLifeAnimation(
            _Controller(),
            {
                "random_seed": 714,
                "plant_modifiers": {
                    "version": 1,
                    "active": ["obstacle"],
                    "strengths": {"obstacle": 1.0},
                },
            },
        )
        animation.set_presentation_context(_context(self.populated))
        self.assertTrue(np.any(animation._plant_blocked))
        state = (
            deepcopy(animation.grid),
            animation.generation,
            animation.phase,
            animation.phase_frame,
            animation.last_step_elapsed,
            animation.last_glider_time,
        )
        rng = animation.random.getstate()
        old_next = deepcopy(animation.next_grid)

        animation.set_presentation_context(_context(self.empty))

        self.assertFalse(np.any(animation._plant_blocked))
        self.assertEqual(
            state,
            (
                animation.grid,
                animation.generation,
                animation.phase,
                animation.phase_frame,
                animation.last_step_elapsed,
                animation.last_glider_time,
            ),
        )
        self.assertEqual(animation.random.getstate(), rng)
        self.assertNotEqual(old_next, animation.next_grid)

    def test_christmas_layout_rebuild_preserves_snowfall_clock_and_rng(self) -> None:
        animation = ChristmasTreeAnimation(
            _Controller(), {"seed": 123, "plant_aware": True}
        )
        animation.set_presentation_context(_context(self.populated))
        animation.generate_frame(0.0, 0)
        self.assertTrue(np.any(animation._plant_obstacle))
        animation.snowflakes = [{"x": 2.0, "y": 7.0, "speed": 1.25, "drift": 0.3}]
        animation._last_update_time = 8.5
        rng = animation.random.getstate()
        authored = animation.authored_params_snapshot()

        animation.set_presentation_context(_context(self.empty))

        self.assertFalse(np.any(animation._plant_obstacle))
        self.assertEqual(
            animation.snowflakes,
            [{"x": 2.0, "y": 7.0, "speed": 1.25, "drift": 0.3}],
        )
        self.assertEqual(animation._last_update_time, 8.5)
        self.assertEqual(animation.random.getstate(), rng)
        self.assertEqual(animation.authored_params_snapshot(), authored)

    def test_ecosystem_and_maze_refresh_geometry_without_resetting_active_worlds(self) -> None:
        ecosystem = LivingEcosystemAnimation(
            _Controller(), {"seed": 88, "plant_aware": True}
        )
        ecosystem.set_presentation_context(_context(self.populated))
        ecosystem_state = (
            ecosystem.grass.tobytes(),
            tuple(
                (item.species, item.pack, item.x, item.y, item.age, item.energy)
                for item in ecosystem.creatures
            ),
            ecosystem._cycle,
            ecosystem._sim_time,
        )
        ecosystem_rng = deepcopy(ecosystem.rng.bit_generator.state)
        ecosystem.set_presentation_context(_context(self.empty))
        self.assertFalse(np.any(ecosystem._plant_canvas_clearance))
        self.assertEqual(
            ecosystem_state,
            (
                ecosystem.grass.tobytes(),
                tuple(
                    (item.species, item.pack, item.x, item.y, item.age, item.energy)
                    for item in ecosystem.creatures
                ),
                ecosystem._cycle,
                ecosystem._sim_time,
            ),
        )
        self.assertEqual(ecosystem.rng.bit_generator.state, ecosystem_rng)

        maze = MazeChaseAnimation(_Controller(), {"seed": 81, "plant_aware": True})
        maze.set_presentation_context(_context(self.populated))
        active = (
            deepcopy(maze.player),
            deepcopy(maze.ghosts),
            set(maze.pellets),
            set(maze.energizers),
            maze.sim_time,
            maze.last_elapsed,
        )
        maze_rng = maze.random.getstate()
        populated_plan = set(maze.initial_pellets)
        maze.set_presentation_context(_context(self.empty))
        self.assertFalse(maze._plant_occluded_cells)
        self.assertNotEqual(maze.initial_pellets, populated_plan)
        self.assertEqual(
            active,
            (
                maze.player,
                maze.ghosts,
                maze.pellets,
                maze.energizers,
                maze.sim_time,
                maze.last_elapsed,
            ),
        )
        self.assertEqual(maze.random.getstate(), maze_rng)

    def test_lazy_mask_layout_caches_invalidate_without_advancing_animation(self) -> None:
        tetris = TetrisAnimation(_Controller(), {"plant_aware": True})
        tetris.set_presentation_context(_context(self.populated))
        self.assertIs(tetris._plant_geometry(), self.populated.plant_masks)
        board = deepcopy(tetris.board)
        pieces = deepcopy(tetris.active_pieces)
        rng = tetris.random.getstate()
        timing = (tetris.last_elapsed, tetris.last_render_elapsed, tetris.next_render_elapsed)
        tetris.set_presentation_context(_context(self.empty))
        self.assertIsNone(tetris._tetris_plant_masks)
        self.assertTrue(tetris.plans_dirty)
        self.assertEqual(tetris.board, board)
        self.assertEqual(tetris.active_pieces, pieces)
        self.assertEqual(tetris.random.getstate(), rng)
        self.assertEqual(
            (tetris.last_elapsed, tetris.last_render_elapsed, tetris.next_render_elapsed),
            (timing[0], None, None),
        )

        flags = WorldFlagsAnimation(_Controller(), {"plant_aware": True})
        flags.set_presentation_context(_context(self.populated))
        flags.generate_frame(2.0, 1)
        self.assertIsNotNone(flags._plant_canvas_key)
        flags.set_presentation_context(_context(self.empty))
        self.assertIsNone(flags._plant_canvas_key)
        self.assertIsNone(flags._plant_canvas)

    def test_remaining_plugin_owned_caches_refresh_without_lifecycle_mutation(self) -> None:
        falling = AsciiDropAnimation(
            _Controller(), {"random_seed": 22, "plant_aware": True}
        )
        falling.set_presentation_context(_context(self.populated))
        self.assertTrue(np.any(falling._plant_clearance))
        falling_state = (
            deepcopy(falling._pieces),
            falling._settled.tobytes(),
            falling._phrase_index,
            falling._next_spawn_time,
            falling._last_time,
        )
        falling_rng = deepcopy(falling._rng.bit_generator.state)
        falling.set_presentation_context(_context(self.empty))
        self.assertFalse(np.any(falling._plant_clearance))
        self.assertEqual(
            falling_state,
            (
                falling._pieces,
                falling._settled.tobytes(),
                falling._phrase_index,
                falling._next_spawn_time,
                falling._last_time,
            ),
        )
        self.assertEqual(falling._rng.bit_generator.state, falling_rng)

        race = CanopyCupAnimation(
            _Controller(),
            {
                "seed": 44,
                "plant_modifiers": {
                    "version": 1,
                    "active": ["obstacle"],
                    "strengths": {"obstacle": 1.0},
                },
            },
        )
        race.set_presentation_context(_context(self.populated))
        self.assertTrue(np.any(race._plant_obstacle_canvas))
        race_state = (
            deepcopy(race.racers),
            race.simulation_time,
            race.accumulator,
            race.fixed_steps,
            deepcopy(race.points),
        )
        race_rng = race.game_rng.getstate()
        race.set_presentation_context(_context(self.empty))
        self.assertFalse(np.any(race._plant_obstacle_canvas))
        self.assertEqual(
            race_state,
            (
                race.racers,
                race.simulation_time,
                race.accumulator,
                race.fixed_steps,
                race.points,
            ),
        )
        self.assertEqual(race.game_rng.getstate(), race_rng)

        text = EmojiArrangerAnimation(
            _Controller(), {"plant_aware": True, "active_columns": 32}
        )
        text.set_presentation_context(_context(self.populated))
        text.generate_frame(0.0, 0)
        self.assertIsNotNone(text._plant_layout_key)
        text.set_presentation_context(_context(self.empty))
        self.assertIsNone(text._plant_layout_key)

        gif = GifAnimation(_Controller(), {"plant_aware": True})
        gif.set_presentation_context(_context(self.populated))
        gif._adjusted_frame_cache[(0, ())] = np.zeros((32 * 138, 3), np.uint8)
        gif._plant_offset_key = ("sentinel",)
        playback = (gif._current_frame_index, gif._next_frame_time)
        gif.set_presentation_context(_context(self.empty))
        self.assertFalse(gif._adjusted_frame_cache)
        self.assertIsNone(gif._plant_offset_key)
        self.assertEqual((gif._current_frame_index, gif._next_frame_time), playback)

    def test_static_diagnostic_frames_redraw_for_new_profile_without_time_or_params_change(self) -> None:
        calibration = PlantCalibrationAnimation(
            _Controller(), {"plant_aware": True, "manual_pattern_index": 6}
        )
        calibration.set_presentation_context(_context(self.populated))
        populated = calibration.generate_frame(13.0, 9).pixels.copy()
        authored = calibration.authored_params_snapshot()

        calibration.set_presentation_context(_context(self.empty))
        empty = calibration.generate_frame(13.0, 9).pixels.copy()

        self.assertTrue(np.any(populated))
        self.assertFalse(np.any(empty))
        self.assertEqual(calibration.authored_params_snapshot(), authored)
        self.assertEqual(calibration._manual_pattern_index(), 6)

        strips = StripOrderAnimation(
            _Controller(),
            {
                "plant_aware": True,
                "hold_seconds": 1.0,
                "pause_seconds": 0.0,
            },
        )
        strips.set_presentation_context(_context(self.populated))
        populated = strips.generate_frame(0.4, 1).pixels.copy()
        key = strips._plant_render_key
        authored = strips.authored_params_snapshot()

        strips.set_presentation_context(_context(self.empty))
        self.assertIsNone(strips._plant_render_key)
        empty = strips.generate_frame(0.4, 1).pixels.copy()

        self.assertIsNotNone(key)
        self.assertFalse(np.array_equal(populated, empty))
        self.assertEqual(strips._active_strip, 0)
        self.assertEqual(strips.authored_params_snapshot(), authored)

    def test_path_and_static_frame_caches_refresh_without_advancing_state(self) -> None:
        chase = PixelChaseAnimation(_Controller(), {"plant_aware": True})
        chase.set_presentation_context(_context(self.populated))
        populated_path = chase._path.copy()
        chase.generate_frame(3.25, 12)
        authored = chase.authored_params_snapshot()

        chase.set_presentation_context(_context(self.empty))

        self.assertFalse(np.array_equal(populated_path, chase._path))
        np.testing.assert_array_equal(chase._path, chase._physical_path)
        self.assertTrue(np.all(chase._path_kind == chase._CLEAR))
        self.assertIsNone(chase._last_step)
        self.assertEqual(chase.authored_params_snapshot(), authored)

        gradient = GradientAnimation(_Controller(), {"plant_aware": True})
        gradient.set_presentation_context(_context(self.populated))
        populated_frame = gradient.generate_frame(2.0, 1).pixels.copy()
        self.assertTrue(gradient._plant_position_cache)
        gradient.set_presentation_context(_context(self.empty))
        self.assertFalse(gradient._plant_position_cache)
        self.assertFalse(gradient._plant_composition_cache)
        self.assertIsNone(gradient._last_static_key)
        empty_frame = gradient.generate_frame(2.0, 1).pixels.copy()
        self.assertFalse(np.array_equal(populated_frame, empty_frame))

    def test_rate_limited_plugins_refresh_geometry_without_mutating_worlds(self) -> None:
        pinball = PinballAnimation(
            _Controller(),
            {
                "seed": 73,
                "plant_modifiers": {
                    "version": 1,
                    "active": ["bumper"],
                    "strengths": {"bumper": 1.0},
                },
            },
        )
        pinball.set_presentation_context(_context(self.populated))
        self.assertTrue(np.any(pinball._plant_obstacle))
        pinball_state = (
            pinball.ball_x,
            pinball.ball_y,
            pinball.ball_vx,
            pinball.ball_vy,
            pinball.score,
            pinball._sim_time,
            pinball.last_elapsed,
        )
        pinball_rng = pinball.random.getstate()
        pinball.last_render_elapsed = 6.0

        pinball.set_presentation_context(_context(self.empty))

        self.assertFalse(np.any(pinball._plant_obstacle))
        self.assertEqual(
            pinball_state,
            (
                pinball.ball_x,
                pinball.ball_y,
                pinball.ball_vx,
                pinball.ball_vy,
                pinball.score,
                pinball._sim_time,
                pinball.last_elapsed,
            ),
        )
        self.assertEqual(pinball.random.getstate(), pinball_rng)
        self.assertIsNone(pinball.last_render_elapsed)

        quest = PixelQuestAnimation(
            _Controller(), {"seed": 17, "plant_aware": True}
        )
        quest.set_presentation_context(_context(self.populated))
        state = quest.logical_state()
        rng = quest.random.getstate()
        timing = (quest.last_elapsed, quest.run_time, quest.mode_time)
        quest.last_render_elapsed = 5.0

        quest.set_presentation_context(_context(self.empty))

        self.assertEqual(quest.logical_state(), state)
        self.assertEqual(quest.random.getstate(), rng)
        self.assertEqual((quest.last_elapsed, quest.run_time, quest.mode_time), timing)
        self.assertIsNone(quest.last_render_elapsed)


if __name__ == "__main__":
    unittest.main()

"""Regression for installation-owned plant state at the strict Scene v2 boundary."""

from __future__ import annotations

import contextlib
import io
import unittest

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.presentation_contracts import (
    ComponentProvider,
    ComponentRef,
    SceneState,
)


class SceneV2ManagerPlantBoundaryTests(unittest.TestCase):
    def test_tetris_starts_with_global_modifiers_outside_local_parameters(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            manager = AnimationManager(
                PreviewLEDController(33, 138), auto_start=False
            )
        manager._launch_animation_loop = lambda: None
        manager.set_plant_modifiers({
            "version": 1,
            "active": ["hue_shift"],
            "strengths": {"hue_shift": 0.9},
        })
        parameters = {
            "seed": 4201,
            "tetromino_count": 48,
            "bot_imperfection": 0.28,
            "fall_rate": 3.2,
            "smooth_drop": False,
            "smooth_drop_strength": 0.0,
            "smooth_drop_max_pieces": 16,
            "render_fps": 100.0,
            "high_density_render_fps": 90.0,
        }
        component = ComponentRef(
            plugin_id="tetris",
            provider=ComponentProvider.PYTHON,
            resolved_parameters=parameters,
        )
        try:
            self.assertTrue(manager.start_scene(SceneState(
                revision=83,
                background=component,
                overlays=(),
                known_python_fallback=component,
            )))
            animation = manager.current_animation
            self.assertEqual(animation.authored_params_snapshot(), parameters)
            self.assertEqual(
                animation.plant_modifier_state().to_dict(),
                {
                    "version": 1,
                    "active": ["hue_shift"],
                    "strengths": {"hue_shift": 0.9},
                },
            )
            manager.set_plant_modifiers({
                "version": 1,
                "active": ["hue_shift"],
                "strengths": {"hue_shift": 0.4},
            })
            frame = manager.render_composed_scene_frame()
            self.assertEqual(frame.pixels.shape, (33 * 138, 3))
            self.assertEqual(
                animation.plant_modifier_state().strengths["hue_shift"], 0.4
            )
        finally:
            manager.stop_animation(clear_leds=False)


if __name__ == "__main__":
    unittest.main()

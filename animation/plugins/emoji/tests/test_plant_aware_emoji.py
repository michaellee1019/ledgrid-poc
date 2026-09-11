"""Focused Scene v2 cache and control coverage for the Emoji animation."""

from __future__ import annotations

import copy
import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.core.presentation_contracts import resolve_scene
from animation.plugins.emoji import EmojiAnimation
from tests.unit.test_composer_slice import _current_scene
from web.composer_final_preview import current_component_catalog


class EmojiSceneV2Tests(unittest.TestCase):
    """Exercise the production resolved-Scene renderer at the installed size."""

    def setUp(self) -> None:
        self.catalog = current_component_catalog()
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    @staticmethod
    def _scene(palette_id: str = "neutral", parameters: dict | None = None) -> dict:
        scene = _current_scene()
        scene["animation"] = {
            "component_id": "emoji", "version": 1, "provider": "python",
            "role": "animation", "parameters": dict(parameters or EmojiAnimation.DEFAULTS),
        }
        scene["look"].update({"palette_id": palette_id, "pace": .7})
        return scene

    def _render_source(self, scene: dict, elapsed: float, animation: EmojiAnimation):
        return animation.render_resolved_scene(
            resolve_scene(scene, self.catalog, monotonic_elapsed=elapsed)
        )

    def test_quantized_scene_phase_is_history_independent_for_every_palette(self) -> None:
        """A fresh target and a 240 Hz warmup use the same 20 Hz source tick."""

        for palette_id in ("neutral", "mist", "spectrum", "ember"):
            with self.subTest(palette=palette_id):
                scene = self._scene(palette_id)
                fresh = EmojiAnimation(self.controller, {})
                fresh_frame = self._render_source(scene, 1.0, fresh)
                self.assertTrue(fresh_frame.changed)
                self.assertEqual(fresh_frame.pixels.shape, (33 * 138, 3))
                self.assertEqual(fresh_frame.pixels.dtype, np.uint8)

                warmed = EmojiAnimation(self.controller, {})
                changed = 0
                for index in range(240):
                    changed += int(self._render_source(scene, index / 240.0, warmed).changed)
                warmed_frame = self._render_source(scene, 1.0, warmed)

                # At the real Scene pace of .7, a 20 Hz phase cache changes 15
                # of 240 manager samples: useful motion without per-sample paint.
                self.assertEqual(changed, 15)
                self.assertAlmostEqual(changed / 240.0, .0625)
                self.assertFalse(warmed_frame.changed)
                np.testing.assert_array_equal(warmed_frame.pixels, fresh_frame.pixels)
                self.assertEqual(warmed.params, dict(EmojiAnimation.DEFAULTS))

    def test_face_scale_and_pulse_controls_remain_visibly_functional(self) -> None:
        baseline = self._render_source(
            self._scene(parameters=dict(EmojiAnimation.DEFAULTS)), 1.0,
            EmojiAnimation(self.controller, {}),
        ).pixels.copy()
        variants = {
            "face": {**EmojiAnimation.DEFAULTS, "face": "heart"},
            "scale": {**EmojiAnimation.DEFAULTS, "scale": 1.8},
            "pulse": {**EmojiAnimation.DEFAULTS, "pulse_hz": 3.0},
        }
        for control, parameters in variants.items():
            with self.subTest(control=control):
                frame = self._render_source(
                    self._scene(parameters=copy.deepcopy(parameters)), 1.0,
                    EmojiAnimation(self.controller, {}),
                ).pixels
                self.assertFalse(np.array_equal(frame, baseline))


if __name__ == "__main__":
    unittest.main()

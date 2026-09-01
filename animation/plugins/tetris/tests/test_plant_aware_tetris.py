"""Scene v2 Tetris palette and plant-intent contract coverage."""

import unittest

from animation.core.manager import PreviewLEDController
from animation.plugins.tetris import (
    TETROMINOS,
    TetrisAnimation,
)


class TetrisSceneV2Tests(unittest.TestCase):
    def setUp(self):
        self.controller = PreviewLEDController(strips=8, leds_per_strip=12)

    def test_semantic_looks_recolor_without_changing_tetromino_identity(self):
        mist_animation = TetrisAnimation(self.controller, {"seed": 4102, "bot_imperfection": 0.0})
        ember_animation = TetrisAnimation(self.controller, {"seed": 4102, "bot_imperfection": 0.0})
        mist_animation._palette_id = "mist"
        ember_animation._palette_id = "ember"
        mist = mist_animation.generate_frame(0.0, 0).pixels.copy()
        ember = ember_animation.generate_frame(0.0, 0).pixels.copy()

        self.assertFalse((mist == ember).all())
        self.assertEqual(
            [(piece.kind, piece.rotation, piece.x, piece.y) for piece in mist_animation.active_pieces],
            [(piece.kind, piece.rotation, piece.x, piece.y) for piece in ember_animation.active_pieces],
        )

    def test_scene_v2_rejects_legacy_presentation_and_plant_aliases(self):
        for alias in ("speed", "rate", "brightness", "plant_aware", "plant_clearance", "plant_modifiers"):
            with self.subTest(alias=alias), self.assertRaises(ValueError):
                TetrisAnimation(self.controller, {alias: 1})

    def test_descriptor_declares_effect_intent_without_legacy_mask_controls(self):
        descriptor = TetrisAnimation.component_descriptor()

        self.assertEqual(descriptor.alpha_behavior.value, "opaque")
        self.assertEqual(descriptor.palette_policy.value, "semantic")
        self.assertEqual(tuple(item.value for item in descriptor.plant_capabilities), ("effect_intent",))
        self.assertFalse(TetrisAnimation.PLANT_MODIFIER_SUPPORT)
        self.assertNotIn("plant_aware", TetrisAnimation(self.controller).get_parameter_schema())


if __name__ == "__main__":
    unittest.main()

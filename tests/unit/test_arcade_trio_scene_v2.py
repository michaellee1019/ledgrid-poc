"""Focused Scene v2 contract checks for the three opaque arcade games."""

from __future__ import annotations

import unittest
from pathlib import Path

from animation.core.manager import PreviewLEDController
from animation.plugins.maze_chase import MazeChaseAnimation
from animation.plugins.pinball import PinballAnimation
from animation.plugins.pixel_quest import PixelQuestAnimation
from web.composer_component_presets import ComponentPresetCatalog
from web.composer_final_preview import current_component_catalog


class ArcadeTrioSceneV2Tests(unittest.TestCase):
    def test_descriptors_are_semantic_opaque_catalog_members(self) -> None:
        catalog = current_component_catalog()
        for animation in (MazeChaseAnimation, PinballAnimation, PixelQuestAnimation):
            with self.subTest(component=animation.COMPONENT_ID):
                descriptor = animation.component_descriptor()
                self.assertEqual(
                    (descriptor.provider.value, descriptor.role.value, descriptor.timing_policy.value,
                     descriptor.alpha_behavior.value, descriptor.palette_policy.value),
                    ("python", "animation", "scaled_context", "opaque", "semantic"),
                )
                self.assertEqual(tuple(capability.value for capability in descriptor.plant_capabilities), ("effect_intent",))
                self.assertIs(catalog.require(provider="python", component_id=animation.COMPONENT_ID, version=1), descriptor)

    def test_authored_cards_are_exactly_fourteen_local_choices(self) -> None:
        root = Path(__file__).resolve().parents[2]
        choices = ComponentPresetCatalog(root, {
            "maze_chase": MazeChaseAnimation._normalized_parameters,
            "pinball": PinballAnimation._normalized_parameters,
            "pixel_quest": PixelQuestAnimation._normalized_parameters,
        })
        self.assertEqual(len(choices.choices("maze_chase")), 5)
        self.assertEqual(len(choices.choices("pinball")), 5)
        self.assertEqual(len(choices.choices("pixel_quest")), 4)

    def test_rejected_global_edits_leave_the_game_state_unchanged(self) -> None:
        controller = PreviewLEDController(strips=33, leds_per_strip=138)
        for animation in (MazeChaseAnimation, PinballAnimation, PixelQuestAnimation):
            with self.subTest(component=animation.COMPONENT_ID):
                instance = animation(controller, {})
                before = dict(instance.params)
                with self.assertRaisesRegex(ValueError, "non-local parameters"):
                    instance.update_parameters({"brightness": .5})
                self.assertEqual(instance.params, before)


if __name__ == "__main__":
    unittest.main()

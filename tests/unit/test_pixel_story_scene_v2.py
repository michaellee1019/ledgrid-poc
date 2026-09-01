"""Focused Scene v2 checks for the four retained pixel-story instruments."""

from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.ascii_drop import AsciiDropAnimation
from animation.plugins.christmas_tree import ChristmasTreeAnimation
from animation.plugins.emoji import EmojiAnimation
from animation.plugins.night_train_windows import NightTrainWindowsAnimation
from web.composer_component_presets import ComponentPresetCatalog
from web.composer_final_preview import current_component_catalog
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel
from web.app import AnimationWebInterface


class PixelStorySceneV2Tests(unittest.TestCase):
    classes = (AsciiDropAnimation, EmojiAnimation, ChristmasTreeAnimation, NightTrainWindowsAnimation)

    def test_catalog_and_checked_preset_counts_are_exact(self) -> None:
        catalog = ComponentPresetCatalog(Path(__file__).resolve().parents[2], {
            animation.COMPONENT_ID: animation._normalized_parameters for animation in self.classes
        })
        self.assertEqual([len(catalog.choices(animation.COMPONENT_ID)) for animation in self.classes], [5, 4, 4, 4])
        component_catalog = current_component_catalog()
        for animation in self.classes:
            descriptor = animation.component_descriptor()
            self.assertEqual((descriptor.provider.value, descriptor.role.value, descriptor.timing_policy.value, descriptor.alpha_behavior.value, descriptor.palette_policy.value), ("python", "animation", "scaled_context", "opaque", "semantic"))
            self.assertIs(component_catalog.require(provider="python", component_id=animation.COMPONENT_ID, version=1), descriptor)

    def test_local_controls_render_opaque_and_reject_legacy_globals(self) -> None:
        controller = PreviewLEDController(strips=33, leds_per_strip=138)
        for animation in self.classes:
            with self.subTest(component=animation.COMPONENT_ID):
                instance = animation(controller, {})
                frame = instance.generate_frame(1.0, 0)
                self.assertEqual(frame.pixels.shape, (33 * 138, 3))
                self.assertEqual(frame.pixels.dtype, np.uint8)
                before = dict(instance.params)
                with self.assertRaisesRegex(ValueError, "non-local parameters"):
                    instance.update_parameters({"brightness": .5})
                self.assertEqual(instance.params, before)

    def test_ascii_phrase_changes_the_deterministic_33_by_138_picture(self) -> None:
        controller = PreviewLEDController(strips=33, leds_per_strip=138)
        shared = {"story": "terminal", "fall_speed": 13.0, "density": .45, "seed": 8088}
        hello = AsciiDropAnimation(controller, {**shared, "phrase": "HELLO"}).generate_frame(1.0, 0).pixels.copy()
        goodbye = AsciiDropAnimation(controller, {**shared, "phrase": "GOODBYE"}).generate_frame(1.0, 0).pixels.copy()
        self.assertGreater(np.count_nonzero(hello != goodbye), 0)

    def test_composer_surface_qualifies_all_four_pixel_story_instruments(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source = (root / "web" / "static" / "js" / "composer_slice.js").read_text(encoding="utf-8")
        css = (root / "web" / "static" / "css" / "composer_slice.css").read_text(encoding="utf-8")
        self.assertIn("label[hidden] { display: none !important; }", css)
        controls = {
            "ascii_drop": ("asciiDropPresetCards", "#asciiPhrase"),
            "emoji": ("emojiAnimationPresetCards", "#emojiFace"),
            "christmas_tree": ("christmasTreePresetCards", "#treeSeason"),
            "night_train_windows": ("nightTrainPresetCards", "#trainRoute"),
        }
        for component_id, (card_id, local_control) in controls.items():
            with self.subTest(component=component_id):
                self.assertIn(f"'{component_id}'", source)
                self.assertIn(card_id, source)
                self.assertIn(local_control, source)
        self.assertIn("label.hidden = !visible", source)
        self.assertIn("['#asciiPhrase'].forEach((selector) => $(selector).addEventListener('input', edit));", source)
        self.assertIn("asciiDropParameters(next.animation.parameters)", source)

    def test_composer_api_serves_every_pixel_story_card(self) -> None:
        client = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True).app.test_client()
        for component_id, expected_count in zip(("ascii_drop", "emoji", "christmas_tree", "night_train_windows"), (5, 4, 4, 4)):
            with self.subTest(component=component_id):
                response = client.get(f"/api/composer/components/{component_id}/presets")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.get_json()["presets"]), expected_count)


if __name__ == "__main__":
    unittest.main()

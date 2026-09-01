"""Focused Scene v2 contracts for the transparent Emoji Message Widget."""

from __future__ import annotations

import unittest

import numpy as np

from animation.core.component_catalog import ComponentCatalog
from animation.core.manager import PreviewLEDController
from animation.plugins.emoji_arranger import EmojiArrangerAnimation
from web.composer_final_preview import current_component_catalog


class EmojiMessageWidgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    def test_descriptor_is_a_scaled_semantic_python_widget_and_is_catalogued(self) -> None:
        descriptor = EmojiArrangerAnimation.component_descriptor()
        self.assertEqual(
            (descriptor.component_id, descriptor.provider.value, descriptor.role.value,
             descriptor.timing_policy.value, descriptor.alpha_behavior.value,
             descriptor.palette_policy.value, tuple(item.value for item in descriptor.plant_capabilities),
             descriptor.fidelity_exceptions),
            ("emoji_arranger", "python", "widget", "scaled_context", "premultiplied_rgba",
             "semantic", ("effect_intent",), ()),
        )
        self.assertIs(
            current_component_catalog().require(provider="python", component_id="emoji_arranger", version=1),
            descriptor,
        )
        self.assertIs(ComponentCatalog([descriptor]).require(
            provider="python", component_id="emoji_arranger", version=1), descriptor)

    def test_transparent_premultiplied_plane_preserves_reveal_and_moves_with_scaled_time(self) -> None:
        widget = EmojiArrangerAnimation(self.controller, {
            "text": "HI🔥", "x_offset": 8, "y_offset": 3, "scroll_speed": 3.0, "pulse_speed": 0.0,
        })
        first = widget.generate_frame(0.0, 0)
        later = widget.generate_frame(1.0, 1)
        self.assertEqual(first.pixels.shape, (33 * 138, 4))
        self.assertTrue(np.all(first.pixels[:, :3] <= first.pixels[:, 3:4]))
        self.assertGreater(np.count_nonzero(first.pixels[:, 3]), 0)
        self.assertGreater(np.count_nonzero(first.pixels[:, 3] == 0), 0)
        self.assertFalse(np.array_equal(first.pixels, later.pixels))

    def test_rejects_legacy_aliases_raw_channels_and_invalid_message_values(self) -> None:
        for parameters, message in (
            ({"brightness": 0.5}, "non-local"),
            ({"speed": 1.0}, "non-local"),
            ({"plant_aware": True}, "non-local"),
            ({"background_red": 2}, "non-local"),
            ({"primary_red": 255}, "non-local"),
            ({"active_columns": 8}, "non-local"),
            ({"text": "hello"}, "unsupported"),
            ({"x_offset": 138}, "x_offset"),
            ({"scroll_speed": 25.0}, "scroll_speed"),
        ):
            with self.subTest(parameters=parameters), self.assertRaisesRegex(ValueError, message):
                EmojiArrangerAnimation(self.controller, parameters)

    def test_valid_updates_are_deterministic_and_invalid_updates_leave_the_last_valid_plane(self) -> None:
        widget = EmojiArrangerAnimation(self.controller, {"text": "A🔥", "pulse_speed": 0.0})
        widget.generate_frame(.5, 0)
        widget.update_parameters({"char_spacing": 3})
        changed = widget.generate_frame(.5, 1)
        self.assertTrue(changed.changed)
        before = dict(widget.params)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            widget.update_parameters({"text": "?"})
        self.assertEqual(widget.params, before)


if __name__ == "__main__":
    unittest.main()

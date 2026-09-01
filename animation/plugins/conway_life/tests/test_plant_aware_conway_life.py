"""Headless optional-plant-input parity for the Scene-v1 Conway overlay."""

from __future__ import annotations

from types import MappingProxyType
import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.core.presentation_contracts import ResolvedScene
from animation.plugins.conway_life import ConwayLifeAnimation


class ConwayOptionalPlantInputTests(unittest.TestCase):
    def _context(self, animation: ConwayLifeAnimation, inputs: dict[str, float]) -> ResolvedScene:
        descriptor = animation.component_descriptor()
        return ResolvedScene(
            canonical_scene=MappingProxyType({"palette_id": "mist"}),
            canonical_bytes=b"headless-neutral",
            digest="0" * 64,
            descriptor=descriptor,
            parameters=MappingProxyType(dict(animation.params)),
            palette=MappingProxyType({"palette_id": "mist"}),
            phase_time=1.0,
            plant_inputs=MappingProxyType(inputs),
        )

    def test_declared_optional_inputs_have_headless_neutral_parity(self) -> None:
        controller = PreviewLEDController(strips=8, leds_per_strip=8)
        config = {"seed": 321, "initial_density": 0.2, "generations_per_second": 5.0}
        left = ConwayLifeAnimation(controller, config)
        right = ConwayLifeAnimation(controller, config)
        neutral = {"foliage_density": 0.0, "globe_proximity": 0.0, "occlusion": 0.0}
        left_frame = left.render_resolved_scene(self._context(left, neutral))
        right_frame = right.render_resolved_scene(self._context(right, neutral))
        self.assertEqual(left.semantic_snapshot(), right.semantic_snapshot())
        np.testing.assert_array_equal(left_frame.pixels, right_frame.pixels)
        self.assertFalse(ConwayLifeAnimation.PLANT_MODIFIER_SUPPORT)
        self.assertNotIn("plant_aware", left.params)


if __name__ == "__main__":
    unittest.main()
